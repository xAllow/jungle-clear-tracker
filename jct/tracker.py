"""Background tracker: waits for games, records the first minutes live and, after the game, the replay.

1. While a game is in progress (client phase "InProgress") and you have Smite, record live up to
   `capture_seconds` of game time. Other roles, ARAM or games already past 1:30 are skipped.
2. When the game is over and the client has been out of queue for a while, open the replay, record the
   positions of those minutes, close it and analyze. If you enter queue meanwhile, it is aborted, closed
   and retried later (pending replays survive restarts).
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import analysis, capture, lol_api
from .lol_api import GameApi

FREE_PHASES = {"None", "Lobby", "EndOfGame"}
GAME_PHASES = {"GameStart", "InProgress", "Reconnect"}
QUEUE_PHASES = {"Matchmaking", "ReadyCheck", "ChampSelect"} | GAME_PHASES
MAX_ATTEMPTS = 3
REPLAY_SPEED = 4.0


class Tracker(threading.Thread):
    def __init__(self, settings: dict[str, Any], patch: dict[str, Any], log: logging.Logger,
                 on_status: Callable[[str], None], on_new_summary: Callable[[], None]):
        super().__init__(daemon=True, name="tracker")
        self.settings = settings
        self.patch = patch
        self.log = log
        self.on_status = on_status
        self.on_new_summary = on_new_summary
        self._halt = threading.Event()
        self.data_dir = Path(settings["data_dir"])
        self.lol_dir = lol_api.find_lol_dir(settings.get("lol_dir", ""))
        self.seen: set = set()
        # Recorded games still in progress, whose result (GameEnd event) is not known yet.
        self.awaiting_result: dict[int, Path] = {}

    # ----------------------------------------------------------------------------------------- control
    def stop(self) -> None:
        self._halt.set()

    @property
    def stopping(self) -> bool:
        return self._halt.is_set()

    def _status(self, text: str) -> None:
        self.on_status(text)

    # ------------------------------------------------------------------------------------ pending list
    @property
    def _pending_file(self) -> Path:
        return self.data_dir / "pending_replays.json"

    def _pending(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self._pending_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save_pending(self, items: list[dict[str, Any]]) -> None:
        self._pending_file.write_text(json.dumps(items, indent=1), encoding="utf-8")

    # ------------------------------------------------------------------------------------------- loop
    def run(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log.info("Tracker started (data folder: %s).", self.data_dir)
        free_since: float | None = None
        last_status = None
        while not self.stopping:
            try:
                phase = lol_api.client_phase(self.lol_dir)
                if phase is None:
                    status = "Waiting for the League client"
                elif phase == "InProgress":
                    free_since = None
                    status = self._handle_game()
                elif phase in FREE_PHASES:
                    free_since = free_since or time.time()
                    pending = self._pending()
                    if pending and self.settings.get("replay", True):
                        if time.time() - free_since >= self.settings.get("idle_before_replay", 30):
                            self._handle_replay(pending)
                        status = f"{len(pending)} replay(s) pending"
                    else:
                        status = "Waiting for a game"
                else:
                    free_since = None
                    status = "In queue / champion select"
                if status != last_status:
                    self._status(status)
                    last_status = status
            except Exception:
                self.log.exception("Unexpected error; the tracker keeps running.")
            # The end-of-game screen only lasts a few seconds: look more often while waiting for it.
            self._halt.wait(2 if self.awaiting_result else 5)
        self._status("Stopped")
        self.log.info("Tracker stopped.")

    def _handle_game(self) -> str:
        api = GameApi()
        if api.get("/replay/playback") is not None:
            return "Watching a replay"  # opened by you: not a game
        players = api.get("/liveclientdata/playerlist")
        if not players:
            return "Game loading"
        game_id = lol_api.current_game_id(self.lol_dir)
        if game_id in self.seen:
            # Recorded game still going on: wait for its GameEnd event to keep the result in the capture.
            path = self.awaiting_result.get(game_id)
            if path:
                win = capture.game_end_result(api)
                if win is not None:
                    capture.append_result(path, win)
                    del self.awaiting_result[game_id]
                    self.log.info("Game %s finished: %s.", game_id, "win" if win else "loss")
                return "In game (recorded, waiting for the end)"
            return "In game (already handled)"
        self.seen.add(game_id)
        riot_id = api.get("/liveclientdata/activeplayername") or ""
        me = lol_api.find_player(players, riot_id)
        if me is None:
            self.log.info("Game %s: you are not playing (spectating?). Not recorded.", game_id)
            return "In game (not recorded)"
        if not lol_api.has_smite(me):
            self.log.info("Game %s: %s without Smite (not jungling). Not recorded.", game_id, me.get("championName"))
            return "In game (not jungling)"
        t = (api.get("/liveclientdata/gamestats") or {}).get("gameTime", 0.0)
        if t > 90:
            self.log.info("Game %s: already at %.0f s, too late for the first clear. Not recorded.", game_id, t)
            return "In game (joined too late)"

        self._status(f"Recording {me.get('championName')} live")
        region = lol_api.current_platform(self.lol_dir) or "GAME"
        path = capture.live(api, self.data_dir, self.settings.get("capture_seconds", 420), game_id, region,
                            lambda: self.stopping, self.log.info)
        self.log.info("Live capture saved: %s", path.name)
        if game_id:
            self.awaiting_result[game_id] = path
        if game_id and self.settings.get("replay", True):
            pending = self._pending()
            pending.append({"game_id": game_id, "match_id": f"{region}_{game_id}", "riot_id": riot_id,
                            "live": path.name, "attempts": 0})
            self._save_pending(pending)
            self.log.info("Replay of %s queued: it will be recorded after the game, when you are out of queue.", game_id)
        else:
            self._analyze(path.name)
        return "In game (recorded)"

    def _searching(self) -> bool:
        if lol_api.client_phase(self.lol_dir) in QUEUE_PHASES:
            return True
        lcu = lol_api.client(self.lol_dir)
        try:
            state = (lcu.get("/lol-lobby/v2/lobby/matchmaking/search-state") or {}) if lcu else {}
        except Exception:
            return False
        return state.get("searchState") in ("Searching", "Found")

    def _handle_replay(self, pending: list[dict[str, Any]]) -> None:
        if GameApi().get("/replay/playback") is not None:
            return  # you are watching a replay yourself: don't touch it
        item = pending[0]
        state = capture.replay_state(self.lol_dir, item["game_id"])
        if state not in ("watch", "download"):
            if state in ("incompatible", "lost", "missingOrExpired", "unsupported", "error"):
                self.log.warning("Replay of %s unavailable (%s); keeping the live capture only.", item["game_id"], state)
                pending.pop(0)
                self._save_pending(pending)
                self._analyze(item["live"])
            return  # not ready yet ("checking"...)
        last = [0.0, False]

        def stop() -> bool:
            if self.stopping:
                return True
            if time.time() - last[0] > 2:  # ask the client at most every 2 s
                last[0], last[1] = time.time(), self._searching()
            return last[1]

        self._status(f"Recording replay of {item['game_id']}")
        try:
            path = capture.replay(GameApi(), self.lol_dir, item["game_id"], item["riot_id"], item["match_id"],
                                  self.data_dir, self.settings.get("capture_seconds", 420), REPLAY_SPEED,
                                  stop, self.log.info)
        except capture.CaptureError as e:
            path = None
            item["attempts"] += 1
            self.log.warning("Replay of %s: %s (attempt %d of %d).", item["game_id"], e, item["attempts"], MAX_ATTEMPTS)
        finally:
            if capture.close_replay(self.lol_dir):
                self.log.info("Replay closed.")
        if path:
            pending.pop(0)
            self._analyze(item["live"])
        elif item["attempts"] >= MAX_ATTEMPTS:
            self.log.warning("Replay of %s dropped after %d attempts.", item["game_id"], MAX_ATTEMPTS)
            pending.pop(0)
            self._analyze(item["live"])
        else:
            self.log.info("Replay of %s will be retried when you are out of queue.", item["game_id"])
        self._save_pending(pending)

    def _analyze(self, live_file: str) -> None:
        rows = analysis.analyze_folder(self.data_dir, self.lol_dir, self.patch, only=[live_file])
        row = next((r for r in rows if r["file"] == live_file), None)
        if row:
            self.log.info("First clear: %s, %s, route %s, scuttles %s, %s.", row["clear_end_mmss"], row["status"],
                          row["route"] or "-", row["early_scuttles"], {True: "win", False: "loss"}.get(row["win"], "result unknown"))
        self.on_new_summary()
