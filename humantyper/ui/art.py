"""ASCII art: an ANSI-Shadow banner with a twinkling starfield, and a density-ramp ASCII face that
dissolves into drifting stardust over a slowly turning spiral nebula.

Everything here is pure Python: functions return grids of (char, tag) cells plus a tag -> colour map;
`widgets.AsciiView` paints them into a tk.Text.
"""
import math
import random

from . import theme as T

# --------------------------------------------------------------------------- banner
_FIG = {
    "H": ["██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "U": ["██╗   ██╗", "██║   ██║", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "M": ["███╗   ███╗", "████╗ ████║", "██╔████╔██║", "██║╚██╔╝██║", "██║ ╚═╝ ██║", "╚═╝     ╚═╝"],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
    "Y": ["██╗   ██╗", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "],
    "P": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    " ": ["   "] * 6,
}
FIG_H = 6
NHUE = 32                                  # gradient buckets along the banner


def figlet(text="HUMAN TYPER"):
    """Six equal-width lines of █ ╗ ╔ ═ ║ ╚ ╝ letters."""
    rows = [""] * FIG_H
    for ch in text.upper():
        g = _FIG[ch]
        rows = [rows[r] + g[r] for r in range(FIG_H)]
    w = max(len(r) for r in rows)
    return [r.ljust(w) for r in rows]


_STAR_GLYPHS = [".", "·", "˙", "'", "`", ",", "+", "*", "✦", "⋆"]


class Banner:
    """Figlet banner centred in a starfield. cells() -> rows of (char, tag)."""

    PAD_X, PAD_Y = 6, 1

    def __init__(self, text="HUMAN TYPER", seed=7, density=0.11, glyph_ok=None):
        self.lines = figlet(text)
        self.fw = len(self.lines[0])
        self.cols = self.fw + 2 * self.PAD_X
        self.rows = FIG_H + 2 * self.PAD_Y
        ok = set(glyph_ok) if glyph_ok else set(".,'`+*")
        dim = [g for g in (".", "·", "˙", "'", "`", ",") if g in ok] or ["."]
        bright = [g for g in ("*", "+", "✦", "⋆") if g in ok] or ["*"]
        rnd = random.Random(seed)
        self.stars = {}                      # (r, c) -> (dim glyph, bright glyph, kind, rate, offset)
        for r in range(self.rows):
            for c in range(self.cols):
                inside = (self.PAD_Y <= r < self.PAD_Y + FIG_H and self.PAD_X <= c < self.PAD_X + self.fw
                          and self.lines[r - self.PAD_Y][c - self.PAD_X] != " ")
                if inside:
                    continue
                # thin out the stars hugging the letters so the lettering stays crisp
                near = (self.PAD_Y - 1 <= r <= self.PAD_Y + FIG_H and
                        self.PAD_X - 1 <= c <= self.PAD_X + self.fw)
                if rnd.random() < density * (0.45 if near else 1.0):
                    self.stars[(r, c)] = (rnd.choice(dim), rnd.choice(bright), rnd.randrange(3),
                                          0.02 + rnd.random() * 0.06, rnd.random())

    @staticmethod
    def tag_colors():
        cols = {}
        for i in range(NHUE):
            col = T.gradient(i / (NHUE - 1.0))
            cols[f"bm{i}"] = col                              # solid letter body
            cols[f"bs{i}"] = T.mix(T.BG, col, 0.52)           # shadow strokes
        for kind, base in enumerate((T.ICE, T.VIOLET, T.CYAN)):
            for lvl in range(4):
                cols[f"s{kind}{lvl}"] = T.mix(T.BG, base, 0.30 + 0.23 * lvl)
        return cols

    def cells(self, phase=0, hue=0.0):
        rows = []
        n = float(self.fw)
        for r in range(self.rows):
            row = []
            for c in range(self.cols):
                ch, tag = " ", None
                fr, fc = r - self.PAD_Y, c - self.PAD_X
                if 0 <= fr < FIG_H and 0 <= fc < self.fw and self.lines[fr][fc] != " ":
                    ch = self.lines[fr][fc]
                    t = abs(((fc / n * 0.9 + hue) % 2.0) - 1.0)     # triangle wave 0..1..0
                    i = max(0, min(NHUE - 1, int(t * (NHUE - 1) + 0.5)))
                    tag = f"{'bm' if ch == '█' else 'bs'}{i}"
                else:
                    st = self.stars.get((r, c))
                    if st:
                        dimg, brg, kind, rate, off = st
                        b = 0.5 + 0.5 * math.sin(6.2832 * (phase * rate + off))
                        lvl = min(3, int(b * 4))
                        ch = brg if lvl == 3 else dimg
                        tag = f"s{kind}{lvl}"
                row.append((ch, tag))
            rows.append(row)
        return rows


# --------------------------------------------------------------------------- face
RAMP = " .'`^\",:;Il!i~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
FW, FH = 56, 30           # face grid (characters)
NTINT, NLVL = 8, 8
_DRIFT = ".'`:+*o"


def _intensity(u, v):
    """Brightness 0..1 of a stylised head + shoulders at normalised coords, or None outside."""
    ue = u / (1 - 0.18 * v) if v > 0 else u
    r2 = ue * ue + v * v
    if r2 <= 1.0:
        b = 0.86 - 0.40 * ue - 0.20 * v
        b *= 1 - 0.75 * r2 ** 3
        if v < -0.52 - 0.30 * ue * ue:                 # hair cap (textured in Face)
            return 0.20 + 0.16 * (1 - ue) / 2
        for ex in (-0.40, 0.38):
            if abs(ue - ex) < 0.28 and -0.34 < v < -0.27 + 0.05 * abs(ue - ex):   # brow
                b = 0.10
            e = ((ue - ex) / 0.20) ** 2 + ((v + 0.13) / 0.075) ** 2
            if e < 1:                                                                # eye
                b = 1.0 if e > 0.45 else 0.04
                if abs(ue - ex - 0.05) < 0.04 and abs(v + 0.15) < 0.035:
                    b = 1.0
            elif ((ue - ex) / 0.30) ** 2 + ((v + 0.13) / 0.15) ** 2 < 1:             # socket shadow
                b *= 0.50
        if 0.0 < ue < 0.15 and -0.05 < v < 0.33:        # nose shadow
            b *= 0.55
        if ((abs(ue) - 0.12) / 0.08) ** 2 + ((v - 0.38) / 0.05) ** 2 < 1:         # nostrils
            b = 0.06
        if abs(ue) < 0.28 and 0.55 < v < 0.60:          # mouth line
            b = 0.05
        elif abs(ue) < 0.20 and 0.61 < v < 0.72:        # lower lip
            b = min(1.0, b * 0.8 + 0.2)
        if v > 0.82:                                    # under-chin shadow
            b *= 0.5
        return max(0.02, min(1.0, b))
    if abs(ue) < 0.42 and 0.75 < v < 1.6:               # neck
        return max(0.12, 0.34 - 0.22 * ue - 0.1 * (v - 0.75))
    if v > 1.18 and abs(ue) < 1.75 - 0.5 * (v - 1.1):   # shoulders
        return max(0.12, min(0.5, 0.36 - 0.18 * ue))
    return None


class Face:
    """ASCII face (density ramp) over a spiral nebula; dissolves left-to-right into drifting stardust."""

    def __init__(self, seed=11, w=FW, h=FH):
        self.w, self.h = w, h
        rnd = random.Random(seed)
        cx, cy = w * 0.5, h * 0.40
        ry = h * 0.385
        rx = ry * (24.0 / 31.0) * 1.7           # terminal cells are ~1.7x taller than wide
        self.cells_b = [[None] * w for _ in range(h)]
        self.rand = [[(rnd.random(), rnd.random(), rnd.random()) for _ in range(w)] for _ in range(h)]
        xs = []
        for y in range(h):
            for x in range(w):
                b = _intensity((x + 0.5 - cx) / rx, (y + 0.5 - cy) / ry)
                if b is None:
                    continue
                if b < 0.34 and rnd.random() < 0.5:          # hair / shadow grain
                    b *= 0.6 + 0.8 * rnd.random()
                self.cells_b[y][x] = max(0.0, min(1.0, b))
                xs.append(x)
        self.x_min, self.x_max = min(xs), max(xs)
        # nebula geometry (polar around a point beside the head)
        gx, gy = w * 0.5, h * 0.46
        self.polar = [[None] * w for _ in range(h)]
        for y in range(h):
            for x in range(w):
                dx, dy = (x + 0.5 - gx) / (w * 0.5), (y + 0.5 - gy) / (h * 0.5)
                r = math.hypot(dx, dy) + 1e-6
                self.polar[y][x] = (r, math.atan2(dy, dx), math.log(r))

    @staticmethod
    def tag_colors():
        cols = {}
        for ti in range(NTINT):
            tint = T.gradient(ti / (NTINT - 1.0))
            for li in range(NLVL):
                f = li / (NLVL - 1.0)
                dark = T.mix(T.BG, tint, 0.55)
                cols[f"f{ti}_{li}"] = T.mix(dark, "#f6f2ff", f ** 0.85)
        for li in range(5):                                   # nebula / stardust
            for ti in range(NTINT):
                tint = T.gradient(ti / (NTINT - 1.0))
                cols[f"n{ti}_{li}"] = T.mix(T.BG, tint, 0.30 + 0.17 * li)
        return cols

    def cells(self, p, phase=0, glitch=True):
        """Grid h x w of (char, tag)."""
        w, h = self.w, self.h
        p = max(0.0, min(1.0, p)) * 0.96
        span = max(1, self.x_max - self.x_min)
        spin = phase * 0.035
        grid = [[(" ", None)] * w for _ in range(h)]
        gone = [[False] * w for _ in range(h)]
        for y in range(h):
            row = self.cells_b[y]
            for x in range(w):
                b = row[x]
                r1, r2, r3 = self.rand[y][x]
                if b is not None and p > 0:
                    key = 0.62 * (x - self.x_min) / span + 0.38 * r1
                    if key < p:
                        gone[y][x] = True
                        b = None
                if b is not None:
                    ti = min(NTINT - 1, int(x / float(w) * NTINT))
                    li = min(NLVL - 1, int(b * NLVL))
                    grid[y][x] = (RAMP[min(len(RAMP) - 1, int(b * (len(RAMP) - 1) + 0.5))], f"f{ti}_{li}")
                    continue
                # nebula background: spiral arms + sparse stars
                r, th, lr = self.polar[y][x]
                arm = 0.5 + 0.5 * math.cos(2.0 * (th - 2.3 * lr + spin))
                v = arm * math.exp(-1.5 * r * r) * (1.1 - 0.3 * r2)
                if r1 < 0.02:
                    v = max(v, 0.9 if (r3 + phase * 0.01 * (0.5 + r2)) % 1.0 < 0.5 else 0.35)
                if v > 0.14:
                    li = min(4, int(v * 5))
                    ch = " .:·'`-~+*"[min(9, int(v * 9.5))] if v < 0.95 else "*"
                    ch = "." if ch == "·" else ch
                    ti = min(NTINT - 1, int((x / float(w) * 0.6 + (1 - min(1.0, r)) * 0.4) * NTINT))
                    grid[y][x] = (ch, f"n{ti}_{li}")
        # stardust: dissolved face cells drift to the right / upwards as chars from the ramp
        if p > 0:
            for y in range(h):
                for x in range(w):
                    if not gone[y][x]:
                        continue
                    b = self.cells_b[y][x]
                    r1, r2, r3 = self.rand[y][x]
                    if b < 0.28 or ((r3 * 97.0 + phase * 0.23) % 1.0) > 0.55:
                        continue
                    dist = p - (0.62 * (x - self.x_min) / span + 0.38 * r1)
                    dx = x + int(dist * w * 0.9 * (0.3 + r2))
                    dy = y + int((r3 - 0.5) * dist * 9)
                    if 0 <= dx < w and 0 <= dy < h and self.cells_b[dy][dx] is None:
                        li = min(4, 1 + int(r2 * 4))
                        ti = min(NTINT - 1, int(dx / float(w) * NTINT))
                        grid[dy][dx] = (_DRIFT[int(r1 * len(_DRIFT)) % len(_DRIFT)], f"n{ti}_{li}")
            if glitch and p > 0.04:
                rnd = random.Random(phase // 2)
                for _ in range(2):
                    if rnd.random() < 0.6:
                        y = rnd.randrange(0, h)
                        s = rnd.choice((-3, -2, -1, 1, 2, 3))
                        row = grid[y]
                        grid[y] = row[-s:] + row[:-s] if s > 0 else row[-s:] + row[:-s]
        return grid
