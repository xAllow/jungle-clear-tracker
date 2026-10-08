"""Start with Windows: a shortcut in the user's Startup folder (no administrator rights needed)."""

import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "Jungle Clear Tracker.lnk"


def startup_folder() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _target() -> tuple[str, str, str]:
    """(program, arguments, working folder) that start the app minimized and tracking."""
    if getattr(sys, "frozen", False):  # PyInstaller exe
        return sys.executable, "--background", str(Path(sys.executable).parent)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    project = Path(__file__).resolve().parent.parent
    return str(pythonw if pythonw.exists() else sys.executable), f'"{project / "run.py"}" --background', str(project)


def is_enabled(folder: Path | None = None) -> bool:
    return ((folder or startup_folder()) / SHORTCUT_NAME).exists()


def enable(folder: Path | None = None) -> None:
    program, args, workdir = _target()
    shortcut = (folder or startup_folder()) / SHORTCUT_NAME
    # WScript.Shell creates a real .lnk without extra dependencies. Values go in as environment variables
    # so paths with spaces or quotes need no escaping.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:JCT_LNK);"
        "$s.TargetPath = $env:JCT_TARGET; $s.Arguments = $env:JCT_ARGS; $s.WorkingDirectory = $env:JCT_DIR;"
        "$s.WindowStyle = 7; $s.Save()"
    )
    env = {**os.environ, "JCT_LNK": str(shortcut), "JCT_TARGET": program, "JCT_ARGS": args, "JCT_DIR": workdir}
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env, check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def disable(folder: Path | None = None) -> None:
    ((folder or startup_folder()) / SHORTCUT_NAME).unlink(missing_ok=True)
