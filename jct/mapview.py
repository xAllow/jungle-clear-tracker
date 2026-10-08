"""Map drawing (matplotlib): heatmap, positions at an instant and routes over the official minimap."""

from typing import Any

import matplotlib.image as mpimg
import numpy as np
from matplotlib.figure import Figure

from .settings import resource

# Area covered by the minimap image (Data Dragon map11.png), in map coordinates.
EXTENT = (-120, 14870, -120, 14980)
FOUNTAINS = ((410, 422), (14298, 14384))
# Length of the recordings (capture_seconds): the map covers the first 7 minutes.
MAP_END = 420
WINDOWS = {
    "start": "0:00 - 1:05 (before camps spawn)",
    "clear": "1:05 - end of each game's first clear",
    "post": "end of first clear - 7:00",
}
WIN_COLOR, LOSS_COLOR, UNKNOWN_COLOR = "#2ecc71", "#e74c3c", "#95a5a6"

_minimap = None


def minimap() -> np.ndarray:
    global _minimap
    if _minimap is None:
        _minimap = mpimg.imread(resource("assets/map11.png"))
    return _minimap


def window_for(route: dict[str, Any], window: str, custom: tuple[float, float]) -> tuple[float, float]:
    end = route.get("clear_end") or 180
    return {"start": (0, 65), "clear": (65, end), "post": (end, MAP_END)}.get(window, custom)


def position_at(track: list, t: float, margin: float = 4.0) -> tuple[float, float] | None:
    before = None
    for p in track:
        if p[0] <= t:
            before = p
            continue
        if before and p[0] - before[0] <= 2 * margin:
            k = (t - before[0]) / (p[0] - before[0] or 1)
            return before[1] + k * (p[1] - before[1]), before[2] + k * (p[2] - before[2])
        return (p[1], p[2]) if abs(p[0] - t) <= margin else None
    return (before[1], before[2]) if before and t - before[0] <= margin else None


def _near_fountain(x: float, y: float) -> bool:
    return any(np.hypot(x - fx, y - fy) < 1600 for fx, fy in FOUNTAINS)


def _blur(grid: np.ndarray, radius: int = 2, passes: int = 3) -> np.ndarray:
    """Repeated box blur (~gaussian) without scipy."""
    kernel = np.ones(2 * radius + 1) / (2 * radius + 1)
    for _ in range(passes):
        grid = np.apply_along_axis(lambda v: np.convolve(v, kernel, mode="same"), 0, grid)
        grid = np.apply_along_axis(lambda v: np.convolve(v, kernel, mode="same"), 1, grid)
    return grid


def heat(tracks: list[list]) -> np.ndarray | None:
    """Every game weighs the same (its points share a total weight of 1); time in the fountain does not count."""
    xs, ys, ws = [], [], []
    for track in tracks:
        pts = [(x, y) for _, x, y in track if not _near_fountain(x, y)]
        for x, y in pts:
            xs.append(x)
            ys.append(y)
            ws.append(1 / len(pts))
    if not xs:
        return None
    grid, _, _ = np.histogram2d(ys, xs, bins=120, range=[[EXTENT[2], EXTENT[3]], [EXTENT[0], EXTENT[1]]], weights=ws)
    grid = _blur(grid)
    grid = np.sqrt(grid / grid.max())  # so rare spots don't vanish next to the hottest one
    return np.ma.masked_less(grid, 0.08)


def draw(fig: Figure, routes: list[dict[str, Any]], side: str, view: str, window: str,
         custom: tuple[float, float], instant: float, player: str, compare: bool) -> None:
    """Draw one map (or two: wins | losses) on `fig`."""
    fig.clear()
    chosen = [r for r in routes if side == "both" or r.get("side") == side]
    groups = (
        [("Wins", [r for r in chosen if r.get("win") is True]), ("Losses", [r for r in chosen if r.get("win") is False])]
        if compare else [("", chosen)]
    )
    axes = fig.subplots(1, len(groups), squeeze=False)[0]
    for ax, (title, items) in zip(axes, groups):
        ax.imshow(minimap(), extent=EXTENT, zorder=0)
        ax.imshow(np.zeros((2, 2)), extent=EXTENT, cmap="gray", alpha=0.35, zorder=0.5)  # dim the map a bit
        tracks = [r["me"] if player == "me" else r["rival"] for r in items]
        shown = 0
        if view == "instant":
            for r, track in zip(items, tracks):
                pos = position_at(track, instant)
                if pos:
                    shown += 1
                    color = WIN_COLOR if r.get("win") else LOSS_COLOR if r.get("win") is False else UNKNOWN_COLOR
                    ax.scatter([pos[0]], [pos[1]], s=60, color=color, edgecolors="white", linewidths=1, zorder=3)
        else:
            pieces = []
            for r, track in zip(items, tracks):
                a, b = window_for(r, window, custom)
                pts = [p for p in track if a <= p[0] <= b]
                if len(pts) > 1:
                    pieces.append((r, pts))
            shown = len(pieces)
            if view == "heat":
                grid = heat([pts for _, pts in pieces])
                if grid is not None:
                    ax.imshow(grid, extent=EXTENT, origin="lower", cmap="inferno", alpha=0.8, zorder=1)
            else:
                for r, pts in pieces:
                    color = WIN_COLOR if r.get("win") else LOSS_COLOR if r.get("win") is False else UNKNOWN_COLOR
                    ax.plot([p[1] for p in pts], [p[2] for p in pts], color=color, alpha=0.5, lw=1.8, zorder=2)
        ax.set_xlim(EXTENT[0], EXTENT[1])
        ax.set_ylim(EXTENT[2], EXTENT[3])
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        label = f"{shown} game{'s' if shown != 1 else ''}"
        ax.set_title(f"{title} · {label}" if title else label, fontsize=10)
    fig.tight_layout()
