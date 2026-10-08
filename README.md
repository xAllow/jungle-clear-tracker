<div align="center">

# Jungle Clear Tracker

**How fast is your first clear, really?**
Exact camp timings, camp order, scuttles and a map of your first 7 minutes, for every jungle game, automatically.

![Windows](https://img.shields.io/badge/Windows-0078D6?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green)

**English** · [Español](README.es.md)

</div>

![Games](docs/games.png)

## Features

- ⏱️ **First clear time** to ~0.5 s, and when each camp died
- 🧭 **Camp order**, including camps done together (`Krugs + Red`)
- 🦀 **Early scuttles** (0, 1 or 2) and on which side
- 📊 **Win rate** by route, by the side you start/end on and by scuttles
- 🗺️ **Map**: heatmap, positions at any second or routes, for you or the enemy jungler, wins vs losses
- 🤖 **Hands-free**: runs in the background, can start with Windows

![Map](docs/map.png)

## How it works

| During the game | After the game |
|---|---|
| If you have Smite, it records your **gold** for the first 7 minutes: every monster kill is a gold jump, so camp timings are exact. | When you are out of queue, it opens the **replay**, pins the camera above the map and records every champion's **position** (~2 min). If you queue up, it stops and retries later. |

Only the game's official local APIs are used. Nothing is read from memory or network traffic, and nothing is shown during your games.

## Getting started

1. Download `JungleClearTracker.exe` from [Releases](../../releases) (or run from source: `pip install -r requirements.txt` then `python run.py`).
2. Open it: the **Setup check** finds your League folder and enables the game's Replay API with one click.
3. Choose a data folder, press **Start** and play.

> [!NOTE]
> The exe is not code-signed, so SmartScreen or your antivirus may warn about it.

## Good to know

- Rules were tuned on real games with Fiddlesticks; other champions may need small adjustments.
- Patch-dependent values (camp positions, scuttle spawn, gold thresholds) live in [`jct/patch.json`](jct/patch.json).
- Only games recorded from now on can be analyzed: replays only open on the patch they were played on.
- Your data stays in the folder you choose (captures, `summaries.csv`, log).

---

<sub>Jungle Clear Tracker isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games, and all associated properties are trademarks or registered trademarks of Riot Games, Inc. · Minimap from Riot's Data Dragon · [MIT License](LICENSE)</sub>
