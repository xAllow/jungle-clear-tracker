"""User settings (%APPDATA%/JungleClearTracker/settings.json) and paths to bundled resources."""

import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "JungleClearTracker"
APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
SETTINGS_FILE = APP_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    # Where captures (.jsonl), summaries and the log are written.
    "data_dir": str(Path.home() / "Documents" / APP_NAME),
    # League of Legends install folder; empty = detect automatically.
    "lol_dir": "",
    # Seconds of each game to record (first clear, scuttles and first back fit in 5 minutes).
    "capture_seconds": 300,
    # Also record positions from the replay after each game (needed for camp order, scuttles and the map).
    "replay": True,
    # Seconds out of queue before opening a replay (you often re-queue right after a game).
    "idle_before_replay": 30,
    "first_run_done": False,
}


def resource(relative: str) -> Path:
    """Path to a file bundled inside the package (works from source and from the PyInstaller exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "jct" / relative


def load() -> dict[str, Any]:
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **stored}


def save(settings: dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def load_patch_data() -> dict[str, Any]:
    """Patch-dependent values (camp coordinates, gold thresholds, scuttle spawn...).

    A `patch.json` next to settings.json overrides the bundled one, so values can be updated without a new build."""
    bundled = json.loads(resource("patch.json").read_text(encoding="utf-8"))
    override = APP_DIR / "patch.json"
    if override.exists():
        try:
            bundled.update(json.loads(override.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return bundled
