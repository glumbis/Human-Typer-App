"""ASCII art: figlet-ish banner and a dithered face that dissolves into pixels."""
import random

_G = {
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "U": ["█   █", "█   █", "█   █", "█   █", " ███ "],
    "M": ["█   █", "██ ██", "█ █ █", "█   █", "█   █"],
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "Y": ["█   █", " █ █ ", "  █  ", "  █  ", "  █  "],
    "P": ["████ ", "█   █", "████ ", "█    ", "█    "],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    " ": ["   ", "   ", "   ", "   ", "   "],
}


def banner(text="HUMAN TYPER"):
    """Returns (lines, kinds): kinds uses 'm' main, 's' shadow, ' ' empty."""
    rows = ["" for _ in range(5)]
    for i, ch in enumerate(text):
        g = _G[ch]
        for r in range(5):
            rows[r] += g[r] + ("" if i == len(text) - 1 else " ")
    w = len(rows[0])
    lines, kinds = [], []
    for r in range(6):
        line, kind = "", ""
        for c in range(w + 1):
            main = r < 5 and c < w and rows[r][c] == "█"
            sh = r > 0 and c > 0 and rows[r - 1][c - 1] == "█"
            if main:
                line, kind = line + "█", kind + "m"
            elif sh:
                line, kind = line + "░", kind + "s"
            else:
                line, kind = line + " ", kind + " "
        lines.append(line)
        kinds.append(kind)
    return lines, kinds


W, H = 104, 88          # face bitmap size in pixels
BLK = 4                 # dissolve tile size
_BAYER8 = [[(((x ^ y) & 1) << 5 | ((x >> 1 ^ y >> 1) & 1) << 3 | ((x >> 2 ^ y >> 2) & 1) << 1
              | ((y & 1) << 4) | ((y >> 1 & 1) << 2) | (y >> 2 & 1)) for x in range(8)] for y in range(8)]


def _intensity(u, v):
    """Brightness 0..1 of a stylised head+neck at normalised coords, or None outside the figure."""
    ue = u / (1 - 0.18 * v) if v > 0 else u            # slightly tapered jaw
    r2 = ue * ue + v * v
    if r2 <= 1.0:
        b = 0.86 - 0.40 * ue - 0.20 * v
        b *= 1 - 0.75 * r2 ** 3
        if v < -0.52 - 0.30 * ue * ue:                 # hair cap
            return 0.14 + 0.16 * (1 - ue) / 2
        for ex in (-0.40, 0.38):
            if abs(ue - ex) < 0.28 and -0.34 < v < -0.27 + 0.05 * abs(ue - ex):   # brow
                b = 0.08
            if ((ue - ex) / 0.20) ** 2 + ((v + 0.13) / 0.075) ** 2 < 1:           # eye
                b = 0.95 if ((ue - ex) / 0.20) ** 2 + ((v + 0.13) / 0.075) ** 2 > 0.45 else 0.02
                if abs(ue - ex - 0.05) < 0.03 and abs(v + 0.15) < 0.03:
                    b = 1.0
            elif ((ue - ex) / 0.30) ** 2 + ((v + 0.13) / 0.15) ** 2 < 1:           # socket shadow
                b *= 0.50
        if 0.0 < ue < 0.15 and -0.05 < v < 0.33:        # nose shadow
            b *= 0.55
        if ((abs(ue) - 0.12) / 0.08) ** 2 + ((v - 0.38) / 0.05) ** 2 < 1:         # nostrils
            b = 0.05
        if abs(ue) < 0.28 and 0.55 < v < 0.60:          # mouth line
            b = 0.04
        elif abs(ue) < 0.20 and 0.61 < v < 0.72:        # lower lip
            b = min(1.0, b * 0.8 + 0.2)
        if v > 0.82:                                    # under-chin shadow
            b *= 0.5
        return max(0.0, min(1.0, b))
    if abs(ue) < 0.42 and 0.75 < v < 1.5:               # neck
        return 0.34 - 0.22 * ue - 0.1 * (v - 0.75)
    if v > 1.18 and abs(ue) < 1.75 - 0.5 * (v - 1.1):   # shoulders
        return max(0.12, min(0.5, 0.36 - 0.18 * ue))
    return None


class Face:
    """1-bit dithered face bitmap; tiles dissolve left-to-right into drifting squares."""

    def __init__(self, seed=11, cx=66.0, cy=33.0, rx=24.0, ry=31.0):
        rnd = random.Random(seed)
        self.bits = [[0] * W for _ in range(H)]
        for y in range(H):
            for x in range(W):
                b = _intensity((x - cx) / rx, (y - cy) / ry)
                if b is None:
                    continue
                self.bits[y][x] = 1 if b > (_BAYER8[y & 7][x & 7] + 0.5) / 64.0 else 0
        self.tw, self.th = W // BLK, H // BLK
        self.tiles = []          # (tx, ty, density, n1, n2, n3)
        xs = []
        for ty in range(self.th):
            for tx in range(self.tw):
                d = sum(self.bits[ty * BLK + j][tx * BLK + i] for j in range(BLK) for i in range(BLK))
                if d:
                    xs.append(tx)
                    self.tiles.append((tx, ty, d / float(BLK * BLK), rnd.random(), rnd.random(), rnd.random()))
        self.x_min, self.x_max = min(xs), max(xs)

    def render(self, p, phase=0, glitch=True):
        """Returns a 'L'-mode PIL image (W x H): white-on-black."""
        from PIL import Image, ImageDraw
        img = Image.new("L", (W, H), 0)
        img.putdata([255 if b else 0 for row in self.bits for b in row])
        p = max(0.0, min(1.0, p)) * 0.94
        if p > 0:
            d = ImageDraw.Draw(img)
            span = max(1, self.x_max - self.x_min)
            for tx, ty, dens, n1, n2, n3 in self.tiles:
                key = 0.62 * (tx - self.x_min) / span + 0.38 * n1
                if key >= p:
                    continue
                x, y = tx * BLK, ty * BLK
                d.rectangle([x, y, x + BLK - 1, y + BLK - 1], fill=0)
                if dens < 0.3 or ((n3 * 97.0 + phase * 0.23) % 1.0) > 0.6:
                    continue
                dist = p - key
                dx = x - int(dist * 90 * (0.3 + n2))
                dy = y + int((n3 - 0.5) * dist * 22)
                sz = 1 + int(n2 * 3.2)
                if 0 <= dx < W - sz and 0 <= dy < H - sz:
                    if n1 < 0.7 or sz < 3:
                        d.rectangle([dx, dy, dx + sz - 1, dy + sz - 1], fill=255)
                    else:                                  # hollow square
                        d.rectangle([dx, dy, dx + sz - 1, dy + sz - 1], outline=255)
            if glitch and p > 0.04:
                rnd = random.Random(phase // 2)
                for _ in range(2):
                    if rnd.random() < 0.6:
                        y = rnd.randrange(0, H - 6)
                        h = rnd.randrange(1, 5)
                        s = rnd.choice((-9, -5, -3, 4, 7))
                        band = img.crop((0, y, W, y + h))
                        img.paste(band, (s, y))
        return img
