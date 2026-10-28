"""Colours, fonts and small helpers for the retro terminal look."""
import os
import sys
import tkinter.font as tkfont

BG = "#061214"
BG2 = "#0c2427"
LINE = "#1e6b5f"        # panel borders
DIM = "#4f8a7e"         # secondary text
TEXT = "#b6f5c8"        # body text
GREEN = "#3dff82"
AMBER = "#ffb13b"
RED = "#ff5a52"
CYAN = "#4de3ff"
WHITE = "#f4f4f4"
BLACK = "#000000"

# lolcat-ish stops: green -> yellow -> orange -> red
STOPS = [(70, 245, 90), (235, 240, 60), (255, 150, 40), (255, 70, 60)]

FONT_CANDIDATES = ("Cascadia Mono", "Consolas", "Lucida Console", "Courier New")
_fonts = {}
_family = None


def resource_path(rel: str) -> str:
    """Path to a bundled resource; works from source and in a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, rel)


def init_fonts(root) -> str:
    global _family
    avail = set(tkfont.families(root))
    _family = next((f for f in FONT_CANDIDATES if f in avail), "Courier New")
    return _family


def font(size=10, bold=False) -> tkfont.Font:
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = tkfont.Font(family=_family or "Consolas", size=size,
                                  weight="bold" if bold else "normal")
    return _fonts[key]


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def gradient(t: float, dim: float = 1.0) -> str:
    """t in 0..1 along STOPS -> '#rrggbb'."""
    t = min(1.0, max(0.0, t)) * (len(STOPS) - 1)
    i = min(int(t), len(STOPS) - 2)
    r, g, b = lerp(STOPS[i], STOPS[i + 1], t - i)
    return "#%02x%02x%02x" % (int(r * dim), int(g * dim), int(b * dim))


def ascii_bar(value, lo, hi, width=12) -> str:
    frac = 0.0 if hi == lo else (float(value) - lo) / (hi - lo)
    n = max(0, min(width, int(round(frac * width))))
    return "[" + "█" * n + "░" * (width - n) + "]"
