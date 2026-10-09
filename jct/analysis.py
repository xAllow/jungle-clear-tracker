"""Analysis of the captures: exact time of each camp, first clear, camp order and scuttles.

Measured vs inferred:
  - Instants of gold, CS and level changes: measured (resolution = capture interval, ~0.5 s).
  - Grouping kills into camps, naming them and deciding where the first clear ends: inferred
    (heuristics tuned on real games; values in patch.json).
"""

import csv
import json
import math
from pathlib import Path
from typing import Any

from . import lol_api

STATUS_TEXT = {
    "clean": "Clean",
    "clean_fight": "Clean, with a fight",
    "interrupted": "Interrupted",
    "possible_invade": "Possible invade",
    "incomplete": "Incomplete",
    "no_data": "No data",
    "no_camps": "No camp data",
}
REASON_TEXT = {
    "death": "you died",
    "fight": "fight (kill/assist)",
    "pause": "long pause without farming",
    "back": "recall",
    "complete": "6 camps done",
    "end_of_capture": "end of capture",
    "late_capture": "capture started late",
}


def mmss(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


# --------------------------------------------------------------------------------------------- loading

def load(path: Path) -> dict[str, Any]:
    header, samples, events, positions, result = {}, [], [], [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            kind = row.get("type")
            if kind == "header":
                header = row
            elif kind == "sample":
                samples.append(row)
            elif kind == "pos":
                positions.append((row["t"], row["p"]))
            elif kind == "events":
                events = row["events"]
            elif kind == "result":  # GameEnd, appended by the tracker when the game finished
                result = row.get("win")
    return {
        "file": path.name,
        "header": header,
        "samples": samples,
        "events": events,
        "result": result,
        "positions": positions,
        "order": [p.get("riot_id") for p in header.get("players", [])],
        "positions_from": path.name if positions else None,
    }


def attach_positions(captures: list[dict[str, Any]]) -> None:
    """Live captures have no positions: they are taken from the replay capture of the same match."""
    replays = {c["header"].get("match_id"): c for c in captures if c["positions"] and c["header"].get("match_id")}
    for c in captures:
        replay = replays.get(c["header"].get("match_id"))
        if not c["positions"] and replay:
            c["positions"], c["order"], c["positions_from"] = replay["positions"], replay["order"], replay["file"]


# ------------------------------------------------------------------------------------------ signals

def increases(samples: list[dict[str, Any]], field: str) -> list[tuple[float, float]]:
    out, prev = [], None
    for s in samples:
        value = s.get(field)
        if value is None:
            continue
        if prev is not None and value > prev:
            out.append((s["t"], value - prev))
        prev = value
    return out


def milestones(samples: list[dict[str, Any]], field: str) -> dict[str, float]:
    """Instant at which the field reaches each new value (CS 10/20/30, level 2/3/4...).
    The value already present in the first sample does not count: we don't know when it was reached."""
    out, prev = {}, None
    for s in samples:
        value = s.get(field)
        if value is None:
            continue
        if prev is not None and value > prev:
            out[str(value)] = s["t"]
        prev = value
    return out


def gold_jumps(samples: list[dict[str, Any]], p: dict[str, Any]) -> list[tuple[float, float]]:
    out, prev = [], None
    for s in samples:
        if s.get("gold") is None:
            continue
        if prev is not None:
            dt = max(s["t"] - prev["t"], 0.0)
            extra = s["gold"] - prev["gold"] - p["passive_gold_per_second"] * dt
            if extra >= p["min_gold_jump"] and s.get("items") == prev.get("items"):
                out.append((s["t"], round(extra)))
        prev = s
    # A small extra (+20) can arrive half a second after a big monster kill (blue, gromp, red...): it belongs
    # to that kill. Counted apart, the group would look like several monsters and rule out blue and gromp.
    merged: list[tuple[float, float]] = []
    for t, n in out:
        if (merged and n <= p.get("bonus_gold_max", 25) and merged[-1][1] >= p["big_jump_gold"]
                and t - merged[-1][0] <= p.get("bonus_gold_window", 1.0)):
            merged[-1] = (merged[-1][0], merged[-1][1] + n)
        else:
            merged.append((t, n))
    return merged


def group_kills(kills: list[tuple[float, float]], gap: float) -> list[dict[str, Any]]:
    """Kills closer than `gap` seconds form one step of the clear (camps done at the same time)."""
    groups: list[dict[str, Any]] = []
    for t, n in kills:
        if groups and t - groups[-1]["end"] <= gap:
            g = groups[-1]
            g["end"] = t
            g["amount"] += n
            g["jumps"].append(n)
        else:
            groups.append({"start": t, "end": t, "amount": n, "jumps": [n]})
    for prev, cur in zip(groups, groups[1:]):
        cur["since_previous"] = round(cur["end"] - prev["end"], 1)
    return groups


def incidents(samples: list[dict[str, Any]], events: list[dict[str, Any]], header: dict[str, Any]) -> list[dict]:
    """What cuts a clear: deaths, fights (your kills/assists) and recalls (a new item after 1:30)."""
    me = (header.get("riot_id") or "").split("#")[0]
    out = []
    for prev, s in zip(samples, samples[1:]):
        if s.get("dead") and not prev.get("dead"):
            out.append({"t": s["t"], "kind": "death"})
        # Only a new item counts: using up the last potion also changes the inventory.
        if s["t"] > 90 and set(s.get("items") or []) - set(prev.get("items") or []):
            out.append({"t": s["t"], "kind": "back"})
    for e in events:
        if e.get("EventName") != "ChampionKill":
            continue
        t = round(e.get("EventTime", 0.0), 2)
        if e.get("VictimName") == me:
            if not any(i["kind"] == "death" and abs(i["t"] - t) < 3 for i in out):
                out.append({"t": t, "kind": "death"})
        elif e.get("KillerName") == me or me in (e.get("Assisters") or []):
            out.append({"t": t, "kind": "fight", "victim": e.get("VictimName")})
    return sorted(out, key=lambda i: i["t"])


# ------------------------------------------------------------------------------------ positions/camps

def position_at(positions: list, index: int, t: float, margin: float = 4.0) -> tuple[float, float] | None:
    before = after = None
    for tp, pts in positions:
        if pts[index] is None:
            continue
        if tp <= t:
            before = (tp, pts[index])
        else:
            after = (tp, pts[index])
            break
    if before and after and after[0] - before[0] <= 2 * margin:
        k = (t - before[0]) / (after[0] - before[0]) if after[0] > before[0] else 0.0
        return (before[1][0] + k * (after[1][0] - before[1][0]), before[1][1] + k * (after[1][1] - before[1][1]))
    nearest = min((m for m in (before, after) if m), key=lambda m: abs(m[0] - t), default=None)
    return tuple(nearest[1]) if nearest and abs(nearest[0] - t) <= margin else None


def scuttle_side(group: dict[str, Any], points: list[list[int]], p: dict[str, Any]) -> str | None:
    """'top' / 'bottom' if the group is a scuttle: a big gold jump, in the river, after the scuttles spawn.

    Scuttles move along the river, so the distance to the river diagonal is used instead of a fixed point,
    away from the Baron and Dragon pits."""
    if group["end"] < p["scuttle_spawn_seconds"] or max(group["jumps"]) < p["scuttle_min_gold"]:
        return None
    x, y = min(points, key=lambda q: abs(q[0] + q[1] - p["river_sum"]))
    in_river = abs(x + y - p["river_sum"]) / math.sqrt(2) <= p["river_width"]
    pits = (p["camps"]["Dragon"], p["camps"]["Baron / Herald / Grubs"])
    if not in_river or any(math.hypot(x - px, y - py) < 700 for px, py in pits):
        return None
    return "top" if x < y else "bottom"


def name_camps(groups: list[dict[str, Any]], capture: dict[str, Any], p: dict[str, Any], window: float = 30.0) -> None:
    """Name each group after the camp you were *working on* since the previous kill (max `window` s).

    Not the position at the kill instant: with ranged damage or drains (e.g. Fiddlesticks' W) monsters
    die when you are already walking to the next camp."""
    riot_id = capture["header"].get("riot_id")
    if not capture["positions"] or riot_id not in capture["order"]:
        return
    index = capture["order"].index(riot_id)
    team = capture["header"].get("team")
    camps = {name: tuple(xy) for name, xy in p["camps"].items()}

    def own(name: str) -> str:
        if team:
            name = name.replace(f"({team})", "(own)")
        return name.replace("(ORDER)", "(enemy)").replace("(CHAOS)", "(enemy)")

    previous_end = None
    for g in groups:
        since = max(previous_end or 0.0, g["start"] - window)
        previous_end = g["end"]
        series = [(t, pts[index]) for t, pts in capture["positions"] if since <= t <= g["end"] and pts[index]]
        point = position_at(capture["positions"], index, g["end"])
        if point:
            g["pos"] = [round(point[0]), round(point[1])]
        if not series:
            continue
        path = [q for _, q in series]
        # For the scuttle, where you were when it died matters, not the way since the previous kill.
        at_kill = [q for t, q in series if t >= g["start"] - 3] or path[-1:]
        side = scuttle_side(g, at_kill, p)
        if side:
            g["scuttle"] = side
            g["camp"] = f"Scuttle {side}"
            continue

        big = [j for j in g["jumps"] if j >= p["big_jump_gold"]]
        if len(big) >= 2:
            # Several big jumps at once: several camps dying together (e.g. blue + gromp or krugs + red
            # drained at the same time). Big jumps come from single-monster camps and the big krug.
            nearest = sorted(
                (min(math.hypot(x - cx, y - cy) for x, y in path), name)
                for name, (cx, cy) in camps.items() if name.startswith(tuple(p["big_jump_camps"]))
            )[: len(big)]
            if all(d <= p["max_camp_distance"] for d, _ in nearest):
                g["distance"] = round(nearest[0][0])
                # Fixed order inside the step, so "Krugs + Red" is the same route in every game.
                g["camp"] = " + ".join(sorted(own(n) for _, n in nearest))
                continue
        # Blue, red and gromp are one monster worth a lot of gold: a group of several kills, or of one
        # small kill, cannot be any of them (it will be wolves, raptors or krugs).
        single = len(g["jumps"]) == 1 and g["jumps"][0] >= p["big_jump_gold"]
        candidates = {
            n: xy for n, xy in camps.items()
            if not n.startswith("Scuttle") and (single or not n.startswith(tuple(p["single_monster_camps"])))
        }
        # Time spent within working distance of each camp (attack range / drain).
        time_near: dict[str, float] = {}
        for (t, (x, y)), (t2, _) in zip(series, series[1:] + [(g["end"], None)]):
            for name, (cx, cy) in candidates.items():
                if math.hypot(x - cx, y - cy) <= p["camp_work_radius"]:
                    time_near[name] = time_near.get(name, 0.0) + max(t2 - t, 0.0)
        closest = min((min(math.hypot(x - cx, y - cy) for x, y in path), n) for n, (cx, cy) in candidates.items())
        g["distance"] = round(closest[0])
        ranking = sorted(time_near.items(), key=lambda kv: -kv[1])
        if not ranking:
            if closest[0] <= p["max_camp_distance"]:
                g["camp"] = own(closest[1])
            continue
        if ranking[0][1] < p["min_work_seconds"]:
            continue  # just walking past a camp is not working it: stays "?"
        if len(ranking) > 1 and ranking[1][1] >= 0.75 * ranking[0][1]:
            g["camp"] = f"{own(ranking[0][0])} / {own(ranking[1][0])}"  # too close to call
        else:
            g["camp"] = own(ranking[0][0])


# ------------------------------------------------------------------------------------------ the clear

def first_clear(groups: list[dict[str, Any]], incid: list[dict], end_gap: float) -> tuple[list, str | None, float | None]:
    """Camps up to the first incident or long pause. Returns (groups, reason it stopped, instant).

    A fight (your kill or assist) does not stop the clear: if you neither die nor recall, it goes on with the
    camps after it. If the clear ends up short, `clear_status` marks it as interrupted by the fight."""
    if not groups:
        return [], None, None
    stops = [i for i in incid if i["kind"] != "fight"]
    before = [i for i in stops if i["t"] < groups[0]["start"]]
    if before:
        return [], before[0]["kind"], before[0]["t"]
    clear = [groups[0]]
    for g in groups[1:]:
        between = [i for i in stops if clear[-1]["end"] <= i["t"] < g["start"]]
        if between:
            return clear, between[0]["kind"], between[0]["t"]
        if g["start"] - clear[-1]["end"] > end_gap:
            return clear, "pause", clear[-1]["end"]
        clear.append(g)
    after = [i for i in incid if i["t"] >= clear[-1]["end"]]
    return clear, (after[0]["kind"] if after else "end_of_capture"), (after[0]["t"] if after else None)


def cut_when_complete(clear: list[dict[str, Any]], own_camps: int) -> tuple[list[dict[str, Any]], bool]:
    """The first clear ends when all your camps are done; whatever comes next is not part of it."""
    done: set[str] = set()
    for i, g in enumerate(clear):
        name = g.get("camp") or ""
        if " / " not in name:  # ambiguous groups don't count as done
            done.update(n for n in name.split(" + ") if n.endswith("(own)"))
        if len(done) >= own_camps:
            return clear[: i + 1], i + 1 < len(clear)
    return clear, False


def camp_order(clear: list[dict[str, Any]]) -> tuple[list[str], bool]:
    """Camp order of the first clear, without repeats. Returns (order, reliable).

    - Consecutive groups with the same name are one camp split in two (e.g. small wolves and big wolf).
    - An ambiguous group ("Raptors / Red"): if one option is the previous camp, it is its continuation;
      otherwise the option not seen yet and not the next group's camp.
    - Camps dying together go in one step: "Blue + Gromp".
    - Not reliable if something stays unknown or ambiguous, or a camp appears twice."""
    options_list = [(g.get("camp") or "?").split(" / ") for g in clear]
    order: list[str] = []
    reliable = True
    for i, options in enumerate(options_list):
        nxt = options_list[i + 1] if i + 1 < len(options_list) else []
        if len(options) > 1:
            if order and order[-1] in options:
                options = [order[-1]]
            else:
                free = [o for o in options if o not in order and o not in (nxt if len(nxt) == 1 else [])]
                if len(free) == 1:
                    options = free
                else:
                    reliable = False
        name = " / ".join(options)
        if name == "?":
            reliable = False
        if not order or order[-1] != name:
            order.append(name)
    loose = [n for step in order for n in step.split(" + ")]
    if len(set(loose)) < len(loose):
        reliable = False
    return [n.replace(" (own)", "") for n in order], reliable


def enemy_ahead(levels: dict[str, float], enemy_levels: dict[str, float], margin: float = 5.0) -> bool:
    """The enemy jungler reaches level 3 or 4 clearly before you.
    In live games the enemy level only updates while visible (fog of war), so this misses many invades."""
    for lvl in ("3", "4"):
        enemy = enemy_levels.get(lvl)
        if enemy is not None and (levels.get(lvl) is None or enemy < levels[lvl] - margin):
            return True
    return False


def clear_status(source: str, reason: str | None, t: float | None, incid: list[dict], levels: dict, enemy_levels: dict,
                 complete: bool, first_sample: float, clear: list[dict] | None = None,
                 fights: list[dict] | None = None) -> dict[str, Any]:
    """`fights`: your kills/assists before the clear ended (e.g. killing whoever invaded you)."""
    fights = fights or []
    if first_sample > 90:
        return {"status": "no_data", "reason": "late_capture", "t": first_sample}
    if source == "milestones":
        back = next((i["t"] for i in incid if i["kind"] == "back"), None)
        early = [i for i in incid if i["kind"] != "back" and (back is None or i["t"] < back)]
        if early:
            return {"status": "interrupted", "reason": early[0]["kind"], "t": early[0]["t"]}
        return {"status": "no_camps", "reason": None, "t": None}
    if complete and fights:
        # A fight before the first camp (an invade or a level 1 fight) or in the middle of the clear.
        when = "before" if clear and fights[0]["t"] < clear[0]["start"] else "during"
        return {"status": "clean_fight", "reason": reason, "t": t, "fight": when, "fight_t": fights[0]["t"]}
    if complete:
        return {"status": "clean", "reason": reason, "t": t}
    if reason == "death":
        return {"status": "interrupted", "reason": reason, "t": t}
    if fights or reason == "fight":
        # You fought and the clear was left short: the fight is what spoiled it.
        return {"status": "interrupted", "reason": "fight", "t": fights[0]["t"] if fights else t}
    if reason == "pause":
        # Going to a stolen camp and finding it empty looks like a long pause.
        return {"status": "possible_invade" if enemy_ahead(levels, enemy_levels) else "interrupted", "reason": reason, "t": t}
    return {"status": "possible_invade" if enemy_ahead(levels, enemy_levels) else "incomplete", "reason": reason, "t": t}


def analyze(capture: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    samples = capture["samples"]
    cs_up = increases(samples, "cs")
    cs_step = min((n for _, n in cs_up), default=None)
    incid = incidents(samples, capture["events"], capture["header"])
    # Gold from your own kills/assists is not jungle gold.
    fights = [i["t"] for i in incid if i["kind"] == "fight"]
    gold = [(t, n) for t, n in gold_jumps(samples, p) if not any(0 <= t - f <= 2 for f in fights)]

    # CS arrives in steps of 10 in the Live Client API, so the active player's gold is the real signal.
    if cs_step is not None and cs_step < 2:
        source, kills = "cs", cs_up
    elif gold:
        source, kills = "gold", gold
    else:
        source, kills = "milestones", []  # replay without a live capture: only level/CS milestones

    groups = group_kills(kills, p["group_gap_seconds"])
    name_camps(groups, capture, p)
    clear, reason, t_end = first_clear([g for g in groups if not g.get("scuttle")], incid, p["clear_end_gap_seconds"])
    clear, cut = cut_when_complete(clear, p["own_camps"])
    if cut:
        reason, t_end = "complete", clear[-1]["end"]
    back = next((i["t"] for i in incid if i["kind"] == "back"), None)
    limit = min(back or p["early_game_seconds"], p["early_game_seconds"])
    scuttles = [{"t": g["end"], "side": g["scuttle"]} for g in groups if g.get("scuttle") and g["end"] <= limit] \
        if capture["positions"] and source != "milestones" else None
    order, reliable = camp_order(clear) if capture["positions"] else (None, False)
    amount = sum(g["amount"] for g in clear)
    complete = amount >= p["min_clear_gold"] if source == "gold" else len(clear) >= 5
    clear_fights = [i for i in incid if i["kind"] == "fight" and clear and i["t"] < clear[-1]["end"]]
    levels, enemy_levels = milestones(samples, "level"), milestones(samples, "rival_level")
    return {
        "file": capture["file"],
        "header": {k: v for k, v in capture["header"].items() if k != "type"},
        "positions_from": capture["positions_from"],
        "source": source,
        "groups": groups,
        "clear": {
            "groups": len(clear),
            "start": clear[0]["start"] if clear else None,
            "end": clear[-1]["end"] if clear else None,
            "gold": amount,
            "order": order,
            "reliable": reliable,
            "back": back,
            **clear_status(source, reason, t_end, incid, levels, enemy_levels, complete,
                           samples[0]["t"] if samples else 0.0, clear, clear_fights),
        },
        "scuttles": scuttles,
        "levels": levels,
        "incidents": incid,
        "result": capture.get("result"),
    }


# ------------------------------------------------------------------------------------------ summaries

def game_result(lol_dir: Path, match_id: str | None) -> tuple[bool | None, int | None]:
    """(win, queue) from the client's match history; includes flex and normals."""
    if not match_id:
        return None, None
    lcu = lol_api.client(lol_dir)
    if not lcu:
        return None, None
    try:
        puuid = (lcu.get("/lol-summoner/v1/current-summoner") or {}).get("puuid")
        game = lcu.get(f"/lol-match-history/v1/games/{str(match_id).split('_')[-1]}") or {}
    except Exception:
        return None, None
    pid = next((i.get("participantId") for i in game.get("participantIdentities", [])
                if (i.get("player") or {}).get("puuid") == puuid), None)
    stats = next((x.get("stats") or {} for x in game.get("participants", []) if x.get("participantId") == pid), None)
    return (bool(stats["win"]) if stats and "win" in stats else None), game.get("queueId")


def camp_half(name: str, team: str | None, p: dict[str, Any]) -> str | None:
    """'top' / 'bottom': half of the jungle (relative to mid lane) the camp is in."""
    base = name.replace(" (enemy)", "").split(" + ")[0]
    if base not in ("Blue", "Gromp", "Wolves", "Raptors", "Red", "Krugs") or team not in ("ORDER", "CHAOS"):
        return None
    owner = team if "(enemy)" not in name else ("CHAOS" if team == "ORDER" else "ORDER")
    x, y = p["camps"][f"{base} ({owner})"]
    return "top" if y > x else "bottom"


def summary(r: dict[str, Any], lol_dir: Path, p: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Flat per-game record (summaries.json / summaries.csv)."""
    h, c = r["header"], r["clear"]
    order, scuttles = c.get("order"), r.get("scuttles")
    # Result: the GameEnd event saved during the game; otherwise the client's match history;
    # otherwise what an earlier analysis knew (the client may be closed now).
    history_win, queue = game_result(lol_dir, h.get("match_id"))
    win = r.get("result") if r.get("result") is not None else history_win
    if previous:
        win = previous.get("win") if win is None else win
        queue = previous.get("queue") if queue is None else queue
    end, start = c.get("end"), c.get("start")
    return {
        "file": r["file"],
        "match_id": h.get("match_id"),
        "date": h.get("captured_at"),
        "champion": h.get("champion"),
        "enemy_jungler": h.get("enemy_jungler"),
        "side": {"ORDER": "blue", "CHAOS": "red"}.get(h.get("team")),
        "status": c.get("status"),
        "reason": c.get("reason"),
        # Only for clean_fight: "before" (the first camp) or "during", and the second of the fight.
        "fight": c.get("fight"),
        "fight_t": round(c["fight_t"], 1) if c.get("fight_t") is not None else None,
        "clear_end": round(end, 1) if end is not None else None,
        "clear_end_mmss": mmss(end) if end is not None else None,
        "clear_duration": round(end - start, 1) if end is not None and start is not None else None,
        "clear_gold": c.get("gold") if r["source"] == "gold" else None,
        "camps": sum(len(step.split(" + ")) for step in order) if order else None,
        "route": " > ".join(order) if order else None,
        "route_reliable": c.get("reliable") if order else None,
        "starts": camp_half(order[0], h.get("team"), p) if order else None,
        "ends": camp_half(order[-1], h.get("team"), p) if order else None,
        "early_scuttles": len(scuttles) if scuttles is not None else None,
        "scuttle_detail": ", ".join(f"{s['side']} {mmss(s['t'])}" for s in scuttles) if scuttles else None,
        "back": round(c["back"], 1) if c.get("back") is not None else None,
        "level_3": r["levels"].get("3"),
        "level_4": r["levels"].get("4"),
        "win": win,
        "queue": queue,
    }


def save_summaries(rows: list[dict[str, Any]], data_dir: Path) -> None:
    rows = sorted(rows, key=lambda x: x.get("date") or "")
    (Path(data_dir) / "summaries.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    if rows:
        # Summaries written by older versions may lack newer columns: use every column seen, newest order first.
        fields: list[str] = []
        for row in reversed(rows):
            fields += [k for k in row if k not in fields]
        with open(Path(data_dir) / "summaries.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, restval="")
            writer.writeheader()
            writer.writerows(rows)


def load_summaries(data_dir: Path) -> list[dict[str, Any]]:
    try:
        return json.loads((Path(data_dir) / "summaries.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def analyze_folder(data_dir: Path, lol_dir: Path, p: dict[str, Any], only: list[str] | None = None) -> list[dict[str, Any]]:
    """(Re)analyze the live captures in data_dir/captures (or only the `only` files) and update the summaries."""
    folder = Path(data_dir) / "captures"
    captures = [load(f) for f in sorted(folder.glob("*.jsonl"))] if folder.exists() else []
    attach_positions(captures)
    existing = {s["file"]: s for s in load_summaries(data_dir)}
    for c in captures:
        if c["header"].get("mode") != "live" or (only and c["file"] not in only):
            continue
        r = analyze(c, p)
        if r["source"] == "gold":  # exact timings only come from live captures
            existing[c["file"]] = summary(r, lol_dir, p, existing.get(c["file"]))
    rows = list(existing.values())
    save_summaries(rows, data_dir)
    return rows


def load_routes(data_dir: Path) -> list[dict[str, Any]]:
    """Positions for the map: per game, you and the enemy jungler, joined with the game's summary."""
    folder = Path(data_dir) / "captures"
    summaries = {s.get("match_id"): s for s in load_summaries(data_dir) if s.get("match_id")}
    latest: dict[str, Path] = {}
    for f in sorted(folder.glob("*_replay_*.jsonl")) if folder.exists() else []:
        with open(f, encoding="utf-8") as fh:
            match_id = json.loads(fh.readline()).get("match_id")
        if match_id:
            latest[match_id] = f  # files are named by date: the last one wins
    routes = []
    for match_id, f in latest.items():
        c = load(f)
        h = c["header"]
        if h.get("riot_id") not in c["order"]:
            continue
        me = c["order"].index(h["riot_id"])
        rival_id = next((x["riot_id"] for x in h.get("players", []) if x.get("jungler") and x.get("team") != h.get("team")), None)
        rival = c["order"].index(rival_id) if rival_id in c["order"] else None
        s = summaries.get(match_id, {})

        def track(i: int | None) -> list[tuple[float, int, int]]:
            return [(t, pts[i][0], pts[i][1]) for t, pts in c["positions"] if i is not None and pts[i]]

        routes.append({
            "match_id": match_id,
            "date": s.get("date") or h.get("captured_at"),
            "champion": h.get("champion"),
            "enemy_jungler": h.get("enemy_jungler"),
            "side": {"ORDER": "blue", "CHAOS": "red"}.get(h.get("team")),
            "win": s.get("win"),
            "clear_end": s.get("clear_end"),
            "me": track(me),
            "rival": track(rival),
        })
    return sorted(routes, key=lambda x: x.get("date") or "")
