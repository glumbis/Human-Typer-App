"""Generate assets/icon.ico, icon.png (512) and icon.icns (galaxy tile with a gradient 'HT'). Dev-only; needs Pillow.

    python tools/make_icon.py
"""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter

H = ["█   █", "█   █", "█████", "█   █", "█   █"]
T = ["█████", "  █  ", "  █  ", "  █  ", "  █  "]
BG = (7, 5, 26)
STOPS = [(155, 92, 255), (77, 124, 255), (92, 225, 255), (255, 92, 200)]


def grad(t):
    t = min(1.0, max(0.0, t)) * (len(STOPS) - 1)
    i = min(int(t), len(STOPS) - 2)
    a, b, f = STOPS[i], STOPS[i + 1], t - i
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def nebula(size):
    """Spiral galaxy glow on the dark indigo tile."""
    img = Image.new("RGB", (size, size), BG)
    px = img.load()
    c = size / 2.0
    for y in range(size):
        for x in range(size):
            dx, dy = (x - c) / c, (y - c) / c
            r = math.hypot(dx, dy) + 1e-6
            arm = 0.5 + 0.5 * math.cos(2.0 * (math.atan2(dy, dx) - 3.0 * math.log(r) + 0.6))
            v = arm * math.exp(-2.2 * r * r) * 0.95
            col = grad(0.15 + 0.55 * min(1.0, r * 1.2))
            px[x, y] = tuple(int(BG[k] + (col[k] - BG[k]) * v * 0.55) for k in range(3))
    return img


def draw(size=256):
    img = nebula(size).convert("RGBA")
    d = ImageDraw.Draw(img)
    rnd = random.Random(5)
    for _ in range(max(10, size // 5)):                       # stars
        x, y = rnd.randrange(size), rnd.randrange(size)
        b = rnd.randrange(120, 255)
        r = 1 if rnd.random() < 0.85 else max(1, size // 120)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(b, b, 255, 255))
    cell = size // 14
    x0 = (size - cell * 11) // 2
    y0 = (size - cell * 5) // 2
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for gi, glyph in enumerate((H, T)):
        for r, row in enumerate(glyph):
            for cc, ch in enumerate(row):
                if ch == "█":
                    x, y = x0 + (gi * 6 + cc) * cell, y0 + r * cell
                    gd.rectangle([x - 2, y - 2, x + cell + 1, y + cell + 1],
                                 fill=grad((gi * 6 + cc) / 10.0) + (150,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(max(2, size // 40))))
    d = ImageDraw.Draw(img)
    for gi, glyph in enumerate((H, T)):
        for r, row in enumerate(glyph):
            for cc, ch in enumerate(row):
                if ch != "█":
                    continue
                x, y = x0 + (gi * 6 + cc) * cell, y0 + r * cell
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=grad((gi * 6 + cc) / 10.0) + (255,))
    d.rectangle([2, 2, size - 3, size - 3], outline=(59, 44, 128, 255), width=max(2, size // 64))
    return img


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "assets", "icon.ico")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    draw(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", out)
    big = draw(512)
    png = os.path.join(root, "assets", "icon.png")
    big.save(png)
    print("wrote", png)
    icns = os.path.join(root, "assets", "icon.icns")
    big.save(icns, format="ICNS")
    print("wrote", icns)


if __name__ == "__main__":
    main()
