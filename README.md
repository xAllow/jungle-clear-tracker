# Jungle Clear Tracker

Measures your **first jungle clear** in League of Legends, game after game, without you doing anything:

- the exact moment each camp dies and when your first clear ends (to ~0.5 s),
- the camp order (including camps done at the same time, like *Krugs + Red*),
- how many scuttles you take early, and on which side,
- win rate by route, by the side of the map you start/end on and by early scuttles,
- a map of your first minutes: heatmap, everyone's position at a given instant, or overlaid routes, for you or the enemy jungler, and wins vs losses.

It runs in the background: start it once (or let it start with Windows) and play.

## How it works

It combines two official local APIs of the game, because each one has what the other lacks:

| | exact camp timings | positions |
|---|---|---|
| **Live game** (Live Client Data API) | yes: every monster kill is a jump in your gold | no |
| **Replay of the same game** (Replay API) | no gold in replays | yes |

1. **During the game** (only if you have Smite), it records your gold, CS, level and items for the first 5 minutes, and keeps the game's result when it ends (`GameEnd` event). If it misses it, the result is taken from the client's match history.
2. **After the game**, once you have been out of queue for 30 s, it asks the client to open the replay, plays the first 5 minutes at x4 (~1.5 min) with a fixed top-down camera and records every champion's position, then closes the replay. If you queue up meanwhile, it stops, closes the replay and retries later.
3. It joins both by game time: each gold jump is assigned to the camp you were working on.

Positions come from the game itself: the camera is pinned above the map and each champion's on-screen position is projected back to map coordinates (median error ~45 map units against Riot's match timeline). The app never reads game memory or network traffic, and shows nothing during your games.

## Requirements

- Windows, League of Legends.
- The game's **Replay API** enabled (`EnableReplayApi=1` in `Config\game.cfg`). The *Setup check* can do it for you (it keeps a `game.cfg.bak`); close the game before doing it.
- Replays only open with the same patch as your client, so games are processed shortly after you play them.

## Install

**Exe:** download `JungleClearTracker.exe` from the Releases page and run it. It is not code-signed, so Windows SmartScreen or your antivirus may warn about it; you can always run it from source instead.

**From source** (Python 3.11+):

```
pip install -r requirements.txt
python run.py
```

## Use

1. The first time, the **Setup check** opens: League folder, client running, account, Replay API and data folder should all be green.
2. Pick a **data folder** and press **Start**. Tick **Start with Windows** to have it start minimized and tracking every time you log in.
3. Play. The **Games** tab fills in after each jungle game; the **Map** tab shows the positions.

Data folder contents:

| file | what |
|---|---|
| `captures/*_live_*.jsonl` | live recording of each game (gold, CS, level, items, events) |
| `captures/*_replay_*.jsonl` | positions of the 10 players from the replay |
| `summaries.json` / `summaries.csv` | one row per game: clear end, route, scuttles, side, result... |
| `pending_replays.json` | games whose replay is still to be recorded |
| `tracker.log` | what the tracker did |

**Re-analyze all games** recomputes every summary from the raw captures (useful after changing `patch.json`).

## Accuracy and limits

- **Measured:** the instant of every gold/CS/level change, and positions (from the replay).
- **Inferred:** grouping kills into camps, naming them and deciding where the clear ends. The rules were tuned on real games with Fiddlesticks (ranged, drains several camps at once); other champions may need adjustments.
- Patch-dependent values (camp coordinates, scuttle spawn time, gold thresholds) live in `jct/patch.json`. A `patch.json` in `%APPDATA%\JungleClearTracker` overrides it without rebuilding.
- The enemy jungler's level in a live game only updates while visible (fog of war), so *possible invade* detection misses many invades.
- Only games recorded from now on can be analyzed: past games have no live recording, and old replays don't open on a new patch.

## Privacy

Captures contain the Riot IDs of the 10 players of each game, as the game reports them. They stay on your computer, in the folder you choose.

## Build the exe

```
powershell -ExecutionPolicy Bypass -File build.ps1
```

## Legal

Jungle Clear Tracker isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games, and all associated properties are trademarks or registered trademarks of Riot Games, Inc.

The minimap image (`jct/assets/map11.png`) comes from Riot's Data Dragon.

## License

MIT
