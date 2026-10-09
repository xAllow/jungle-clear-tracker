"""Recording a game (live) and its replay (positions).

Each capture is a JSON Lines file:
  {"type": "header", ...}                       first line
  {"type": "sample", "t", "cs", "level", ...}   whenever something changes
  {"type": "pos", "t", "p": [[x, y] | null]}    replay only; one entry per player, in header["players"] order
  {"type": "events", "events": [...]}           Live Client events (kills, objectives...)

Why both: in a live game the API gives your exact gold (each monster kill is a gold jump, so camp
timings are exact) but no positions; in a replay there is no gold, but with a fixed top-down camera
every champion's screen position can be projected onto the map.
"""

import json
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from . import lol_api
from .lol_api import GameApi

Log = Callable[[str], None]
Stop = Callable[[], bool]
WATCH_COMPONENT = {"componentType": "replay-button_match-history"}


class CaptureError(Exception):
    """Expected failure (player not in the game, replay unavailable...): logged, and the tracker carries on."""


def _open(out_dir: Path, mode: str, champion: str) -> tuple[TextIO, Path]:
    folder = Path(out_dir) / "captures"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y%m%d_%H%M%S}_{mode}_{champion}.jsonl"
    return open(path, "w", encoding="utf-8"), path


def _write(f: TextIO, row: dict[str, Any]) -> None:
    f.write(json.dumps(row, ensure_ascii=False) + "\n")
    f.flush()


def _header(mode: str, players: list[dict[str, Any]], me: dict[str, Any], rival: dict[str, Any] | None,
            game_time: float, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "header",
        "mode": mode,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "estimated_start": time.time() - game_time,
        "riot_id": me.get("riotId"),
        "champion": me.get("championName"),
        "team": me.get("team"),
        "enemy_jungler": rival.get("championName") if rival else None,
        "players": [
            {
                "riot_id": p.get("riotId"),
                "champion": p.get("championName"),
                "team": p.get("team"),
                "jungler": lol_api.has_smite(p),
            }
            for p in players
        ],
        **extra,
    }


def _sample(api: GameApi, me_id: str, rival_id: str | None, with_gold: bool) -> tuple[dict | None, list | None]:
    stats = api.get("/liveclientdata/gamestats")
    players = api.get("/liveclientdata/playerlist")
    if stats is None or players is None:
        return None, None
    by_id = {p.get("riotId"): p for p in players}
    me = by_id.get(me_id, {})
    row: dict[str, Any] = {
        "type": "sample",
        "t": round(stats.get("gameTime", 0.0), 2),
        "cs": me.get("scores", {}).get("creepScore"),
        "level": me.get("level"),
        "dead": me.get("isDead"),
        "items": sorted(i.get("itemID") for i in me.get("items", [])),
    }
    if rival_id and rival_id in by_id:
        row["rival_cs"] = by_id[rival_id].get("scores", {}).get("creepScore")
        row["rival_level"] = by_id[rival_id].get("level")
    if with_gold:
        # Only the active player's gold exists, and only in a live game (not in replays).
        active = api.get("/liveclientdata/activeplayer")
        if active and "currentGold" in active:
            row["gold"] = round(active["currentGold"], 1)
    return row, players


def _changed(row: dict[str, Any], previous: dict[str, Any] | None) -> bool:
    return previous is None or any(row.get(k) != previous.get(k) for k in row if k not in ("t", "tp"))


def _save_events(api: GameApi, f: TextIO) -> None:
    events = api.get("/liveclientdata/eventdata")
    if events is not None:
        _write(f, {"type": "events", "events": events.get("Events", [])})


def game_end_result(api: GameApi) -> bool | None:
    """True/False once the game has ended (GameEnd event, from the active player's point of view)."""
    events = (api.get("/liveclientdata/eventdata") or {}).get("Events", [])
    end = next((e for e in events if e.get("EventName") == "GameEnd"), None)
    return None if end is None else end.get("Result") == "Win"


def append_result(path: Path, win: bool) -> None:
    """Add the game's result to a live capture (read by the analysis before asking the client)."""
    with open(path, "a", encoding="utf-8") as f:
        _write(f, {"type": "result", "win": win})


def live(api: GameApi, out_dir: Path, max_seconds: float, game_id: int | None, region: str,
         stop: Stop, log: Log) -> Path:
    """Record the running game until `max_seconds` of game time, the end of the game or `stop()`."""
    players = api.wait_for("/liveclientdata/playerlist", every=1.0, stop=stop)
    riot_id = api.get("/liveclientdata/activeplayername")
    if not players or not riot_id:
        raise CaptureError("The game API did not answer.")
    me = lol_api.find_player(players, riot_id)
    if me is None:
        raise CaptureError(f"{riot_id} is not in this game.")
    rival = lol_api.enemy_jungler(players, me)
    start = (api.get("/liveclientdata/gamestats") or {}).get("gameTime", 0.0)
    f, path = _open(out_dir, "live", me.get("championName", "x"))
    match_id = f"{region}_{game_id}" if game_id else None
    _write(f, _header("live", players, me, rival, start, {"match_id": match_id}))
    log(f"Recording {me.get('championName')} live (enemy jungler: {rival and rival.get('championName')}).")

    previous, silent_since, last_events = None, None, 0.0
    while not stop():
        row, _ = _sample(api, me["riotId"], rival and rival["riotId"], with_gold=True)
        if row is None:
            silent_since = silent_since or time.time()
            if time.time() - silent_since > 10:
                log("The game is over.")
                break
        else:
            silent_since = None
            if _changed(row, previous):
                _write(f, row)
                previous = row
            if row["t"] >= max_seconds:
                break
            # Events are lost when the game closes, so they are saved periodically.
            if time.time() - last_events > 30:
                _save_events(api, f)
                last_events = time.time()
        time.sleep(0.25)
    _save_events(api, f)
    f.close()
    return path


def open_replay(lol_dir: Path, game_id: int, max_wait: float = 300.0) -> None:
    """Ask the client to download (if needed) and open the replay of `game_id`."""
    lcu = lol_api.client(lol_dir)
    if not lcu:
        raise CaptureError("The League client is not running.")
    game = lcu.get(f"/lol-match-history/v1/games/{game_id}")
    if not game:
        raise CaptureError(f"Game {game_id} is not in the match history.")
    lcu.post(f"/lol-replays/v1/metadata/{game_id}/create/gameVersion/{game['gameVersion']}"
             f"/gameType/{game['gameType']}/queueId/{game['queueId']}")
    requested, end = False, time.time() + max_wait
    while True:
        state = (lcu.get(f"/lol-replays/v1/metadata/{game_id}") or {}).get("state")
        if state == "watch":
            break
        if state == "download" and not requested:
            lcu.post(f"/lol-replays/v1/rofls/{game_id}/download/graceful", WATCH_COMPONENT)
            requested = True
        elif state in ("incompatible", "lost", "missingOrExpired", "unsupported", "error"):
            raise CaptureError(f"Replay {game_id} cannot be played (state: {state}).")
        if time.time() > end:
            raise CaptureError(f"Replay {game_id} is still not ready (state: {state}).")
        time.sleep(2)
    lcu.post(f"/lol-replays/v1/rofls/{game_id}/watch", WATCH_COMPONENT)


def replay_state(lol_dir: Path, game_id: int) -> str | None:
    """watch, download, checking, lost... Registers the replay in the client if needed."""
    lcu = lol_api.client(lol_dir)
    if not lcu:
        return None
    meta = lcu.get(f"/lol-replays/v1/metadata/{game_id}")
    if not meta:
        game = lcu.get(f"/lol-match-history/v1/games/{game_id}")
        if not game:
            return None  # not in the match history yet
        lcu.post(f"/lol-replays/v1/metadata/{game_id}/create/gameVersion/{game['gameVersion']}"
                 f"/gameType/{game['gameType']}/queueId/{game['queueId']}")
        meta = lcu.get(f"/lol-replays/v1/metadata/{game_id}") or {}
    return meta.get("state")


def _positions(players: list[dict[str, Any]], order: list[str], camera: dict[str, Any],
               width: int, height: int) -> list[list[int] | None]:
    by_id = {p.get("riotId"): p for p in players}
    out: list[list[int] | None] = []
    for riot_id in order:
        point = None
        screen = (by_id.get(riot_id) or {}).get("screenPositionCenter")
        if screen:
            sx, sy = (float(v) for v in screen.split(","))
            point = lol_api.project(sx, sy, camera, width, height)
        out.append([round(point[0]), round(point[1])] if point else None)
    return out


def replay(api: GameApi, lol_dir: Path, game_id: int, riot_id: str, match_id: str, out_dir: Path,
           max_seconds: float, speed: float, stop: Stop, log: Log) -> Path | None:
    """Open the replay, play it from 0:00 at `speed` up to `max_seconds` and record everyone's positions.

    Returns None (and deletes the partial file) if `stop()` interrupts it."""
    open_replay(lol_dir, game_id)
    players = api.wait_for("/liveclientdata/playerlist", every=1.0, limit=180, stop=stop)
    if not players:
        raise CaptureError("The replay did not open in time.")
    if api.get("/replay/playback") is None:
        raise CaptureError("The Replay API is disabled (enable it in the setup check).")
    me = lol_api.find_player(players, riot_id)
    if me is None:
        raise CaptureError(f"{riot_id} is not in this replay.")
    rival = lol_api.enemy_jungler(players, me)
    width, height = lol_api.game_resolution(lol_dir)

    # Jump to 0:00 paused and let the replay rebuild the scene: right after a seek, positions are wrong.
    # A short margin is enough at 0:00, when nobody has left the fountain yet.
    api.post("/replay/playback", {"time": 0.0, "paused": True})
    for _ in range(40):
        time.sleep(0.5)
        if not (api.get("/replay/playback") or {}).get("seeking", True):
            break
    time.sleep(0.5)
    api.post("/replay/render", lol_api.TOP_DOWN_CAMERA)
    api.post("/replay/playback", {"paused": False, "speed": speed})
    length = (api.get("/replay/playback") or {}).get("length", 1e9)

    f, path = _open(out_dir, "replay", me.get("championName", "x"))
    _write(f, _header("replay", players, me, rival, 0.0,
                      {"match_id": match_id, "speed": speed, "resolution": [width, height]}))
    order = [p.get("riotId") for p in players]
    log(f"Recording replay positions of game {game_id} (x{speed:g}, up to {max_seconds:.0f} s).")

    previous, silent_since, aborted = None, None, False
    while True:
        if stop():
            aborted = True
            break
        playback = api.get("/replay/playback") or {}
        if playback.get("seeking"):
            time.sleep(0.25)
            continue
        # The game moves the camera: set it again on every read and use the state it reports back.
        camera = api.post("/replay/render", lol_api.TOP_DOWN_CAMERA)
        row, listing = _sample(api, me["riotId"], rival and rival["riotId"], with_gold=False)
        if row is None:
            silent_since = silent_since or time.time()
            if time.time() - silent_since > 10:
                log("The replay was closed.")
                aborted = True
                break
            continue
        silent_since = None
        row["tp"] = round(playback.get("time", 0.0), 2)
        if _changed(row, previous):
            _write(f, row)
            previous = row
        if camera and listing:
            _write(f, {"type": "pos", "t": row["t"], "p": _positions(listing, order, camera, width, height)})
        if row["t"] >= max_seconds or playback.get("time", 0.0) >= length - 1:
            break
    api.post("/replay/playback", {"paused": True})
    _save_events(api, f)
    f.close()
    if aborted:
        # A partial capture is useless for the analysis: drop it so the whole game is retried later.
        path.unlink(missing_ok=True)
        return None
    return path


def close_replay(lol_dir: Path) -> bool:
    """Close the replay window. Never during a real game: both the client phase and the Replay API are checked."""
    in_game = lol_api.client_phase(lol_dir) in ("GameStart", "InProgress", "Reconnect")
    if in_game or GameApi().get("/replay/playback") is None:
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(["taskkill", "/IM", lol_api.GAME_PROCESS], capture_output=True, creationflags=flags)
    for _ in range(16):  # up to 8 s to close gracefully
        time.sleep(0.5)
        if GameApi().get("/replay/playback") is None:
            break
    in_game = lol_api.client_phase(lol_dir) in ("GameStart", "InProgress", "Reconnect")
    if not in_game and GameApi().get("/replay/playback") is not None:
        subprocess.run(["taskkill", "/F", "/IM", lol_api.GAME_PROCESS], capture_output=True, creationflags=flags)
    return True
