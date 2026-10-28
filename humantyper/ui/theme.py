"""Colours, fonts and small helpers for the retro terminal look."""
import os
import sys
import tkinter.font as tkfont

from .. import plat

# deep-space galaxy palette
BG = "#07051a"          # near-black indigo
BG2 = "#0d0a26"         # panels / active toggle background
PANEL = "#0a0822"       # opaque panels (input / log / face) over the galaxy
BG3 = "#161040"         # slider troughs, subtle fills
LINE = "#3b2c80"        # panel borders (dim violet)
DIM = "#8279b8"         # secondary text
TEXT = "#d9d6ff"        # body text (pale lavender)
ICE = "#bfe6ff"         # ice-blue highlight
VIOLET = "#9b5cff"
LAVENDER = "#b794ff"    # violet that stays readable as small text
BLUE = "#4d7cff"
CYAN = "#5ce1ff"
PINK = "#ff5cc8"
ERR = "#ff6b8b"
WHITE = "#f2f0ff"
BLACK = "#000000"
# legacy aliases (widgets / app use semantic names below)
ACCENT = CYAN           # primary accent: ok / active / typing
WARN = PINK             # armed / paused / typos
INPUT_BG = "#050314"

# nebula stops: violet -> blue -> cyan -> magenta
STOPS = [(155, 92, 255), (77, 124, 255), (92, 225, 255), (255, 92, 200)]

# Windows names first (unchanged behaviour), then the monospaced faces macOS ships with.
FONT_CANDIDATES = ("Consolas", "Cascadia Mono", "SF Mono", "Menlo", "Monaco",
                   "Lucida Console", "Courier New")
# Tk on macOS renders points at 72 dpi (Windows: 96), so text looks ~25% smaller there.
MAC_SIZE_FACTOR = 1.25
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
    _family = next((f for f in FONT_CANDIDATES if f in avail), "Menlo" if plat.IS_MAC else "Courier New")
    return _family


def size_factor() -> float:
    return MAC_SIZE_FACTOR if plat.IS_MAC else 1.0


def scale_size(size):
    """Logical font size -> size actually requested from Tk (identity on Windows)."""
    f = size_factor()
    return size if f == 1.0 else max(1, int(round(size * f)))


def font(size=10, bold=False) -> tkfont.Font:
    key = (size, bold, size_factor())
    if key not in _fonts:
        _fonts[key] = tkfont.Font(family=_family or "Consolas", size=scale_size(size),
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


def rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def hexc(c) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c)


def mix(a: str, b: str, t: float) -> str:
    """Blend hex colour a->b by t (0..1)."""
    return hexc(lerp(rgb(a), rgb(b), t))


def unmeasured_ok(root, fnt, chars):
    """Subset of `chars` whose rendered width equals the font's cell width (safe in monospace grids)."""
    w = fnt.measure("M")
    return [c for c in chars if fnt.measure(c) == w]
