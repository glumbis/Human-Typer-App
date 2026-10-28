"""Generate assets/icon.ico (pixel-block 'HT' on a dark teal tile). Dev-only; needs Pillow.

    python tools/make_icon.py
"""
import os

from PIL import Image, ImageDraw

H = ["█   █", "█   █", "█████", "█   █", "█   █"]
T = ["█████", "  █  ", "  █  ", "  █  ", "  █  "]
STOPS = [(70, 245, 90), (235, 240, 60), (255, 150, 40), (255, 70, 60)]


def grad(t):
    t *= len(STOPS) - 1
    i = min(int(t), len(STOPS) - 2)
    a, b, f = STOPS[i], STOPS[i + 1], t - i
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def draw(size=256):
    img = Image.new("RGBA", (size, size), (6, 18, 20, 255))
    d = ImageDraw.Draw(img)
    cell = size // 13
    w = cell * 11 + cell            # two glyphs of 5 + 1 gap = 11 cells
    x0 = (size - cell * 11) // 2
    y0 = (size - cell * 5) // 2
    d.rectangle([2, 2, size - 3, size - 3], outline=(30, 107, 95, 255), width=max(2, size // 64))
    for gi, glyph in enumerate((H, T)):
        for r, row in enumerate(glyph):
            for c, ch in enumerate(row):
                if ch != "█":
                    continue
                x = x0 + (gi * 6 + c) * cell
                y = y0 + r * cell
                sh = cell // 3
                d.rectangle([x + sh, y + sh, x + cell + sh - 1, y + cell + sh - 1], fill=(20, 70, 62, 255))
    for gi, glyph in enumerate((H, T)):
        for r, row in enumerate(glyph):
            for c, ch in enumerate(row):
                if ch != "█":
                    continue
                x = x0 + (gi * 6 + c) * cell
                y = y0 + r * cell
                col = grad((gi * 6 + c) / 10.0)
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=col + (255,))
    return img


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "assets", "icon.ico")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    draw(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", out)


if __name__ == "__main__":
    main()
