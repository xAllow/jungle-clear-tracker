"""Local League of Legends APIs: Live Client Data + Replay API (game, port 2999) and the client API (LCU).

Only the official local endpoints are used: nothing is read from game memory or network traffic.
"""

import math
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import requests
import urllib3

# Both local APIs use self-signed certificates.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

GAME_URL = "https://127.0.0.1:2999"
DEFAULT_LOL_DIR = Path(r"C:\Riot Games\League of Legends")
PRODUCT_SETTINGS = Path(
    r"C:\ProgramData\Riot Games\Metadata\league_of_legends.live\league_of_legends.live.product_settings.yaml"
)
GAME_PROCESS = "League of Legends.exe"

# Fixed top-down camera over the centre of the map: the whole map is on screen, so each champion's
# screenPositionCenter can be projected back to map coordinates (validated against Riot's match
# timeline: median error ~45 map units).
TOP_DOWN_CAMERA = {
    "cameraAttached": False,
    "cameraMode": "fps",  # never use "tps": it crashes the game
    "cameraPosition": {"x": 7400, "y": 16000, "z": 7400},
    "cameraRotation": {"x": 0, "y": 90, "z": 0},
    "fieldOfView": 60,
    "farClip": 60000,
    "cameraMoveSpeed": 0,
}
GROUND_HEIGHT = 50.0
OFF_SCREEN = 1e6  # the game reports ~3.4e38 for anything that is not on screen


class GameApi:
    """Live Client Data and Replay API (exist only while a game or a replay is open)."""

    def __init__(self, base: str = GAME_URL, timeout: float = 2.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = False

    def get(self, path: str) -> Any:
        """JSON response, or None if the API does not answer (no game/replay open)."""
        try:
            r = self.session.get(self.base + path, timeout=self.timeout)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def post(self, path: str, body: dict[str, Any]) -> Any:
        try:
            r = self.session.post(self.base + path, json=body, timeout=self.timeout)
        except requests.RequestException:
            return None
        return r.json() if r.status_code == 200 and r.content else None

    def wait_for(self, path: str, every: float = 2.0, limit: float | None = None, stop=None) -> Any:
        """Wait until `path` returns something non-empty. None on timeout or when `stop()` is True."""
        end = time.time() + limit if limit else None
        while True:
            data = self.get(path)
            if data:
                return data
            if (end and time.time() > end) or (stop and stop()):
                return None
            time.sleep(every)


class ClientApi:
    """League client API (LCU). Uses the short-lived credentials in the client's lockfile."""

    def __init__(self, lol_dir: Path):
        lockfile = Path(lol_dir) / "lockfile"
        if not lockfile.exists():
            raise RuntimeError("The League client is not running (no lockfile).")
        _, _, port, password, protocol = lockfile.read_text().split(":")
        self.base = f"{protocol}://127.0.0.1:{port}"
        self.auth = ("riot", password)

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        # "Connection: close" avoids spurious 400 responses when reusing connections with the LCU.
        return requests.request(
            method,
            self.base + path,
            auth=self.auth,
            verify=False,
            timeout=15,
            headers={"Accept": "application/json", "Connection": "close"},
            **kwargs,
        )

    def get(self, path: str) -> Any:
        r = self._request("GET", path)
        return r.json() if r.status_code == 200 and r.content else None

    def post(self, path: str, body: Any = None) -> requests.Response:
        return self._request("POST", path, json=body)


def find_lol_dir(configured: str = "") -> Path:
    """League install folder: the configured one, the one the Riot client registered, or the default."""
    if configured and Path(configured).exists():
        return Path(configured)
    try:
        text = PRODUCT_SETTINGS.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r'product_install_full_path:\s*"?([^"\n]+)"?', text)
        if match and Path(match.group(1)).exists():
            return Path(match.group(1))
    except OSError:
        pass
    return DEFAULT_LOL_DIR


def client(lol_dir: Path) -> ClientApi | None:
    try:
        return ClientApi(lol_dir)
    except (RuntimeError, OSError, ValueError):
        return None


def client_phase(lol_dir: Path) -> str | None:
    """Gameflow phase (None, Lobby, Matchmaking, ChampSelect, InProgress, EndOfGame...), None if closed."""
    lcu = client(lol_dir)
    if not lcu:
        return None
    try:
        return lcu.get("/lol-gameflow/v1/gameflow-phase")
    except requests.RequestException:
        return None


def current_riot_id(lol_dir: Path) -> str | None:
    lcu = client(lol_dir)
    try:
        me = (lcu.get("/lol-summoner/v1/current-summoner") or {}) if lcu else {}
    except requests.RequestException:
        return None
    return f"{me['gameName']}#{me['tagLine']}" if me.get("gameName") else None


def current_platform(lol_dir: Path) -> str | None:
    """Platform of the account (EUW1, NA1...), used to build match ids like EUW1_123456."""
    lcu = client(lol_dir)
    try:
        platform = lcu.get("/lol-platform-config/v1/namespaces/LoginDataPacket/platformId") if lcu else None
    except requests.RequestException:
        return None
    return platform if isinstance(platform, str) and platform else None


def current_game_id(lol_dir: Path) -> int | None:
    lcu = client(lol_dir)
    try:
        session = (lcu.get("/lol-gameflow/v1/session") or {}) if lcu else {}
    except requests.RequestException:
        return None
    return (session.get("gameData") or {}).get("gameId") or None


def is_game_running() -> bool:
    """The game process (a match or a replay) is open."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {GAME_PROCESS}"],
            capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except OSError:
        return False
    return GAME_PROCESS.lower() in out.lower()


def game_cfg(lol_dir: Path) -> Path:
    return Path(lol_dir) / "Config" / "game.cfg"


def replay_api_enabled(lol_dir: Path) -> bool:
    try:
        text = game_cfg(lol_dir).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return re.search(r"^EnableReplayApi\s*=\s*1\s*$", text, re.M) is not None


def enable_replay_api(lol_dir: Path) -> str:
    """Add EnableReplayApi=1 under [General] in game.cfg (with a .bak copy). Returns a message for the user."""
    cfg = game_cfg(lol_dir)
    if not cfg.exists():
        return f"Could not find {cfg}."
    if is_game_running():
        return "Close the game (match or replay) first: it rewrites game.cfg when it exits."
    if replay_api_enabled(lol_dir):
        return "The Replay API is already enabled."
    raw = cfg.read_bytes()
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    shutil.copy2(cfg, cfg.with_suffix(".cfg.bak"))
    lines = raw.split(newline)
    if b"[General]" in [l.strip() for l in lines]:
        index = [l.strip() for l in lines].index(b"[General]")
        lines.insert(index + 1, b"EnableReplayApi=1")
    else:
        lines = [b"[General]", b"EnableReplayApi=1"] + lines
    cfg.write_bytes(newline.join(lines))
    return f"Replay API enabled (backup: {cfg.with_suffix('.cfg.bak').name})."


def game_resolution(lol_dir: Path) -> tuple[int, int]:
    """Game resolution (Width/Height in game.cfg); needed to project screen positions."""
    try:
        text = game_cfg(lol_dir).read_text(encoding="utf-8", errors="ignore")
        return int(re.search(r"^Width=(\d+)", text, re.M).group(1)), int(re.search(r"^Height=(\d+)", text, re.M).group(1))
    except (OSError, AttributeError, ValueError):
        return 1920, 1080


def project(sx: float, sy: float, camera: dict[str, Any], width: int, height: int) -> tuple[float, float] | None:
    """Screen pixel -> point on the ground (map x, y), using the real camera state from /replay/render."""
    if sx > OFF_SCREEN or sy > OFF_SCREEN:
        return None
    pos, rot = camera["cameraPosition"], camera["cameraRotation"]
    yaw, pitch = math.radians(rot["x"]), math.radians(rot["y"])
    forward = (math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch))
    right = (math.cos(yaw), 0.0, -math.sin(yaw))
    up = (
        forward[1] * right[2] - forward[2] * right[1],
        forward[2] * right[0] - forward[0] * right[2],
        forward[0] * right[1] - forward[1] * right[0],
    )
    focal = (height / 2) / math.tan(math.radians(camera["fieldOfView"]) / 2)
    u, v = (sx - width / 2) / focal, -(sy - height / 2) / focal
    ray = [forward[k] + u * right[k] + v * up[k] for k in range(3)]
    if ray[1] >= 0:
        return None
    k = (GROUND_HEIGHT - pos["y"]) / ray[1]
    return (pos["x"] + k * ray[0], pos["z"] + k * ray[2])


def has_smite(player: dict[str, Any]) -> bool:
    spells = player.get("summonerSpells") or {}
    return any("Smite" in (s or {}).get("rawDisplayName", "") for s in spells.values())


def find_player(players: list[dict[str, Any]], riot_id: str) -> dict[str, Any] | None:
    name = riot_id.split("#")[0].lower()
    for p in players:
        candidates = (p.get("riotId", ""), p.get("riotIdGameName", ""), p.get("summonerName", ""))
        if any(c and c.lower() in (riot_id.lower(), name) for c in candidates):
            return p
    return None


def enemy_jungler(players: list[dict[str, Any]], me: dict[str, Any]) -> dict[str, Any] | None:
    return next((p for p in players if p.get("team") != me.get("team") and has_smite(p)), None)
