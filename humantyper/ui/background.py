"""Full-window animated ASCII galaxy drawn on a tk.Canvas.

The backdrop is a fine character grid (small mono font). Every frame a numpy field is evaluated over
the grid -- domain-warped nebula clouds, two rotating spiral galaxies, an orbiting black hole with a
Doppler-bright accretion disk, twinkling stars, shooting stars, supernova shock rings, keystroke
ripples and glitch tears -- and mapped to a brightness ramp of characters plus a colour bucket.

Rendering trick: one canvas text item per (row, colour bucket). Each item holds the full row with
only that bucket's characters and spaces elsewhere, so a monospace font keeps every layer aligned.
~90 rows x 16 buckets instead of ~15k per-cell items, and only changed strings are re-configured.
"""
import math
import random
import time
import tkinter as tk

import numpy as np

from . import theme as T

RAMP = " .'`,:;-~=+*o%#@"          # dark -> bright (plain ASCII: same width in every mono font)
PROBE_CHARS = ""                    # kept for API compatibility (no exotic glyphs needed)
NLEV = len(RAMP) - 1
HUES = ("#9b5cff", "#4d7cff", "#5ce1ff", "#ff5cc8", "#e9e4ff")   # violet, blue, cyan, pink, star-white
TONES = (0.50, 0.95)        # brightness tones per hue, mixed toward the background
K = len(HUES) * len(TONES)
TAU = 2 * math.pi


def quantize(v):
    """Brightness (>= 0) -> ramp level 0..NLEV (monotonic, clamped)."""
    return max(0, min(NLEV, int(v * NLEV + 0.5)))


def _mix(hex_a, hex_b, t):
    a, b = T.rgb(hex_a), T.rgb(hex_b)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def palette():
    return [_mix(T.BG, h, tone) for h in HUES for tone in TONES]


class GalaxyBackground:
    def __init__(self, master, fnt=None, glyphs=None):
        self.master = master
        self.fnt = T.font(6)
        self.canvas = tk.Canvas(master, bg=T.BG, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lower(self.canvas)                      # Canvas.lower() would mean tag_lower
        self.pal = palette()
        self.ramp = np.array(list(RAMP), dtype="<U1")
        self.w = self.h = self.rows = self.cols = 0
        self.t = 0.0
        self.bright = 0.0
        self.speed = 1.0
        self._last = time.monotonic()
        self._items = []
        self._state = []
        self._mask = None
        self._neb = None
        self._text_rects, self._opaque = [], []
        self._zones = []
        self._focus = None
        self._rnd = random.Random()
        self._shoots = []       # [x, y, vx, vy, age, life]
        self._ripples = []      # [x, y, r, vr, age, life, strength]
        self._tears = []        # [row, shift, frames]
        self._next_shoot = 2.0
        self._next_nova = 8.0
        self._last_key = 0.0
        self._cfg_id = None
        self.canvas.bind("<Configure>", self._on_configure)

    # ---- layout ----------------------------------------------------------
    def _on_configure(self, e):
        if self._cfg_id:
            self.canvas.after_cancel(self._cfg_id)
        self._cfg_id = self.canvas.after(120, self.rebuild)

    def set_zones(self, rects):
        """Rects where keystroke ripples may appear (visible parts of the backdrop)."""
        self._zones = list(rects)

    def set_focus(self, g1, g2):
        """Centres (x, y) in pixels of the two galaxies (free areas of the window)."""
        new = (tuple(g1), tuple(g2))
        if new != self._focus:
            self._focus = new
            if self.w:
                self._geometry()

    def set_mask(self, text_rects, opaque_rects):
        """No characters are drawn inside foreground text boxes (padded) or under opaque widgets."""
        self._text_rects = [(a - 6, b - 3, c + 6, d + 3) for (a, b, c, d) in text_rects]
        self._opaque = list(opaque_rects)
        self._build_mask()

    def _build_mask(self):
        if not self.rows:
            return
        m = np.zeros((self.rows, self.cols), dtype=bool)
        for (x0, y0, x1, y1) in self._text_rects + self._opaque:
            c0, c1 = max(0, int(x0 // self.cw)), min(self.cols, int(math.ceil(x1 / self.cw)))
            r0, r1 = max(0, int(y0 // self.ch)), min(self.rows, int(math.ceil(y1 / self.ch)))
            if c1 > c0 and r1 > r0:
                m[r0:r1, c0:c1] = True
        self._mask = m

    def masked(self, x, y):
        if self._mask is None:
            return False
        r, c = int(y // self.ch), int(x // self.cw)
        return 0 <= r < self.rows and 0 <= c < self.cols and bool(self._mask[r, c])

    def rebuild(self):
        self._cfg_id = None
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        self.w, self.h = w, h
        self.cw = max(1, self.fnt.measure("M"))
        self.ch = max(1, self.fnt.metrics("linespace"))
        self.cols, self.rows = w // self.cw + 1, h // self.ch + 1
        c.delete("bg")
        self._items, self._state = [], []
        self._used = [[True, True] for _ in range(K)]
        for r in range(self.rows):
            ids = [c.create_text(0, r * self.ch, anchor="nw", text="", fill=self.pal[k], font=self.fnt,
                                 tags=("bg",)) for k in range(K)]
            self._items.append(ids)
            self._state.append([""] * K)
        c.tag_lower("bg")
        xs = (np.arange(self.cols) + 0.5) * self.cw
        ys = (np.arange(self.rows) + 0.5) * self.ch
        self.X, self.Y = np.meshgrid(xs, ys)
        self.S = float(min(w, h))
        rng = np.random.default_rng(7)
        self.grain = rng.random((self.rows, self.cols))
        # sparse star field: twinkle phase/speed per star, ~2.5% of cells
        self.star = rng.random((self.rows, self.cols)) < 0.035
        self.star_ph = rng.random((self.rows, self.cols)) * TAU
        self.star_sp = 0.6 + rng.random((self.rows, self.cols)) * 2.4
        self._star_idx = np.nonzero(self.star)
        self._star_ph, self._star_sp = self.star_ph[self._star_idx], self.star_sp[self._star_idx]
        self._neb = None
        self._geometry()
        self._build_mask()
        self.step(force=True)

    def _geometry(self):
        w, h = self.w, self.h
        f = self._focus or ((w * 0.80, h * 0.86), (w * 0.90, h * 0.08))
        R = self.S
        #            cx, cy, radius, tilt, rotation angle, spin direction
        self.gals = [(f[0][0], f[0][1], R * 0.24, 0.80, 0.25, 1.0),
                     (f[1][0], f[1][1], R * 0.20, 0.60, -0.5, -1.3)]

    # ---- events ------------------------------------------------------------
    def _random_free_point(self, rects=None):
        rects = rects or self._zones or [(0, 0, self.w, self.h)]
        for _ in range(20):
            x0, y0, x1, y1 = self._rnd.choice(rects)
            x, y = self._rnd.uniform(x0, x1), self._rnd.uniform(y0, y1)
            if not self.masked(x, y):
                return x, y
        return None

    def supernova(self, x=None, y=None):
        if x is None:
            p = self._random_free_point([(0, 0, self.w, self.h)])
            if not p:
                return
            x, y = p
        self._ripples.append([x, y, 0.0, self.S * 0.22, 0.0, 2.6, 1.0])

    def keystroke(self):
        now = time.monotonic()
        if now - self._last_key < 0.12 or not self.w:
            return
        self._last_key = now
        p = self._random_free_point()
        if p:
            self._ripples.append([p[0], p[1], 0.0, self.S * 0.30, 0.0, 0.9, 0.55])

    def _spawn_shoot(self):
        x = self._rnd.uniform(-0.1, 1.1) * self.w
        ang = self._rnd.uniform(0.25, 0.6) * (1 if x < self.w / 2 else -1)
        sp = self.S * self._rnd.uniform(0.7, 1.2)
        vx = math.sin(ang) * sp
        vy = math.cos(ang) * sp
        self._shoots.append([x, -10.0, vx, vy, 0.0, self._rnd.uniform(0.9, 1.6)])

    # ---- frame ------------------------------------------------------------
    def step(self, typing=False, force=False):
        """Advance by the real elapsed time and redraw the rows whose text changed."""
        if not self.rows:
            return
        now = time.monotonic()
        dt = min(0.5, now - self._last)
        self._last = now
        k = min(1.0, dt * 1.5)
        self.bright += ((1.0 if typing else 0.0) - self.bright) * k
        self.speed += ((2.4 if typing else 1.0) - self.speed) * k
        dts = dt * self.speed
        self.t += dts
        self._events(dt, dts, typing)
        B, H = self._field(self.t)
        self._full = force
        self._render(B, H)

    def _events(self, dt, dts, typing):
        self._next_shoot -= dt * (3.0 if typing else 1.0)
        if self._next_shoot <= 0:
            self._spawn_shoot()
            self._next_shoot = self._rnd.uniform(1.5, 4.5)
        self._next_nova -= dt
        if self._next_nova <= 0:
            self.supernova()
            self._next_nova = self._rnd.uniform(7, 16)
        if self._rnd.random() < (0.10 if typing else 0.03):
            self._tears.append([self._rnd.randrange(self.rows), self._rnd.choice((-3, -2, 2, 3, 5)),
                                self._rnd.randint(1, 3)])
        for s in self._shoots:
            s[0] += s[2] * dt
            s[1] += s[3] * dt
            s[4] += dt
        self._shoots = [s for s in self._shoots if s[4] < s[5] and -50 < s[0] < self.w + 50]
        for rp in self._ripples:
            rp[2] += rp[3] * dt
            rp[4] += dt
        self._ripples = [rp for rp in self._ripples if rp[4] < rp[5]]

    def _win(self, cx, cy, rx, ry):
        """Grid slices covering the pixel box (cx +- rx, cy +- ry), or None if off-grid."""
        c0, c1 = max(0, int((cx - rx) // self.cw)), min(self.cols, int((cx + rx) // self.cw) + 1)
        r0, r1 = max(0, int((cy - ry) // self.ch)), min(self.rows, int((cy + ry) // self.ch) + 1)
        if c1 <= c0 or r1 <= r0:
            return None
        return (slice(r0, r1), slice(c0, c1))

    def _nebula(self, t):
        X, Y, S = self.X, self.Y, self.S
        x, y = X / S, Y / S
        u = x * 3.2 + 0.7 * np.sin(y * 2.3 + t * 0.11)
        v = y * 3.2 + 0.7 * np.sin(x * 1.9 - t * 0.09)
        c = (np.sin(u * 1.3 + t * 0.07) * np.sin(v * 1.1 - t * 0.05)
             + 0.55 * np.sin(u * 2.9 - v * 2.1 + t * 0.13)
             + 0.30 * np.sin(u * 5.7 + v * 4.3 - t * 0.21))
        cloud = np.clip((c + 0.7) / 2.1, 0, 1) ** 1.5 * 1.05 * (0.75 + 0.5 * self.grain)
        Hn = (0.5 + 0.5 * np.sin(u * 0.7 - v * 0.5 + t * 0.04)) * 1.6     # violet->blue->(cyan) by region
        Hn = np.where(np.sin(u * 0.9 + v * 1.3 - t * 0.03) > 0.55, 3.0, Hn)  # pink filaments
        return cloud, cloud * Hn

    def _field(self, t):
        X, Y, S = self.X, self.Y, self.S
        # --- nebula: domain-warped interfering waves (slow, so recomputed every other frame) ---
        self._nframe = getattr(self, "_nframe", 0) + 1
        if self._nframe % 2 == 1 or self._neb is None or self._neb[0].shape != X.shape:
            self._neb = self._nebula(t)
        B, HW = self._neb[0].copy(), self._neb[1].copy()
        # --- spiral galaxies (only evaluated inside their own bounding box) ---
        for (gx, gy, R, tilt, rot, spin) in self.gals:
            sl = self._win(gx, gy, R * 2.4, R * 2.4)
            if sl is None:
                continue
            dx, dy = X[sl] - gx, Y[sl] - gy
            ca, sa = math.cos(rot), math.sin(rot)
            px, py = dx * ca + dy * sa, (-dx * sa + dy * ca) / tilt
            r = np.hypot(px, py) / R + 1e-6
            th = np.arctan2(py, px)
            arm = np.cos(2.0 * (th - spin * t * 0.35) + 3.2 * np.log(r + 0.04))
            arm = np.clip(arm, 0, 1) ** 2
            fall = np.exp(-r * 1.7)
            core = np.exp(-(r / 0.10) ** 2)
            g = core * 1.8 + arm * fall * (0.8 + 1.0 * self.grain[sl]) + fall * 0.30
            g = g * (r < 2.4)
            B[sl] += g
            hg = np.clip(r * 2.2, 0, 1) * 2.0 + 0.4          # core white-ish -> cyan -> blue arms
            hg = np.where(core > 0.35, 4.0, hg)
            HW[sl] += g * hg
        # --- black hole orbiting the first galaxy ---
        gx, gy, R = self.gals[0][0], self.gals[0][1], self.gals[0][2]
        ang = t * 0.12
        bx, by = gx + math.cos(ang) * R * 0.75, gy + math.sin(ang) * R * 0.30
        rr = S * 0.045
        sl = self._win(bx, by, rr * 3.5, rr * 1.6)
        if sl is not None:
            dx, dy = X[sl] - bx, Y[sl] - by
            ex, ey = dx / rr, dy / (rr * 0.32)
            re = np.hypot(ex, ey)
            ang_d = np.arctan2(ey, ex) - t * 2.0
            disk = np.exp(-((re - 2.3) / 0.7) ** 2) * (0.75 + 0.6 * np.cos(ang_d))
            rp = np.hypot(dx, dy) / rr
            photon = np.exp(-((rp - 1.05) / 0.16) ** 2) * 1.2
            bh = disk * 1.3 + photon
            shadow = rp < 0.95
            B[sl] = np.where(shadow, 0.0, B[sl] + bh)
            HW[sl] = np.where(shadow, 0.0, HW[sl] + bh * np.where(np.sin(ang_d) > 0, 3.0, 2.0))
        # --- ripples (supernova shock rings / keystrokes) ---
        for (cx, cy, r, _vr, age, life, st) in self._ripples:
            wdt = S * 0.012 + r * 0.06
            ext = r + 3 * wdt + S * 0.06
            sl = self._win(cx, cy, ext, ext)
            if sl is None:
                continue
            d = np.hypot(X[sl] - cx, Y[sl] - cy)
            fade = (1 - age / life) * st
            val = np.exp(-((d - r) / wdt) ** 2) * fade * 1.4
            fl = max(0.0, 1 - age * 2.5) * 2.0 * st
            if fl > 0:
                val = val + np.exp(-(d / (S * 0.03)) ** 2) * fl
            B[sl] += val
            HW[sl] += val * 3.0
        # --- twinkling stars (only the star cells) ---
        si = self._star_idx
        tw = 0.5 + 0.5 * np.sin(t * self._star_sp + self._star_ph)
        sb = 0.25 + tw ** 2 * 1.2
        B[si] += sb
        HW[si] += sb * 4.0
        # --- shooting stars: bright head + fading trail ---
        L = S * 0.22
        for (sx, sy, vx, vy, age, life) in self._shoots:
            sp = math.hypot(vx, vy) or 1.0
            ux, uy = vx / sp, vy / sp
            tx, ty = sx - ux * L, sy - uy * L
            sl = self._win((sx + tx) / 2, (sy + ty) / 2, abs(sx - tx) / 2 + 2 * self.cw,
                           abs(sy - ty) / 2 + 2 * self.ch)
            if sl is None:
                continue
            dx, dy = X[sl] - sx, Y[sl] - sy
            along = -(dx * ux + dy * uy)                     # distance behind the head
            perp = np.abs(dx * uy - dy * ux)
            trail = (along > -self.cw) & (along < L) & (perp < self.ch * 0.6)
            val = np.where(trail, (1 - np.clip(along, 0, L) / L) ** 1.5 * 1.6 * (1 - age / life), 0.0)
            B[sl] += val
            HW[sl] += val * 4.0
        H = HW / np.maximum(B, 1e-6)
        B *= 0.78 + 0.32 * self.bright
        return B, H

    def _render(self, B, H):
        lvl = np.clip((B * NLEV - 0.35).astype(int), 0, NLEV)
        if self._mask is not None:
            lvl[self._mask] = 0
        chars = self.ramp[lvl]
        hue = np.clip(np.rint(H), 0, len(HUES) - 1).astype(int)
        tone = np.clip((B * 2.0).astype(int), 0, len(TONES) - 1)
        bucket = hue * len(TONES) + tone
        # glitch tears: shift a few rows sideways for a couple of frames (never into masked cells)
        keep = []
        for tr in self._tears:
            r = tr[0]
            if 0 <= r < self.rows:
                rolled = np.roll(chars[r], tr[1])
                rb = np.roll(bucket[r], tr[1])
                if self._mask is not None:
                    rolled = np.where(self._mask[r], " ", rolled)
                chars[r], bucket[r] = rolled, rb
            tr[2] -= 1
            if tr[2] > 0:
                keep.append(tr)
        self._tears = keep
        blank = chars == " "
        c = self.canvas
        n = self.cols * 4
        rows = range(self.rows)
        if self._mask is not None:
            rows = [r for r in rows if not self._mask[r].all()]
        half = None if self._full else self._nframe % 2
        if half is not None:
            rows = [r for r in rows if r % 2 == half]          # interleave: half the rows per frame
        present = np.bincount(bucket[~blank], minlength=K)
        st = self._state
        for k in range(K):
            # skip a colour layer that is empty now and was already cleared on these rows
            used = self._used[k]
            halves = (0, 1) if half is None else (half,)
            if not present[k] and not any(used[h] for h in halves):
                continue
            for h in halves:
                used[h] = bool(present[k])
            layer = np.where((bucket == k) & ~blank, chars, " ")
            raw = np.ascontiguousarray(layer).tobytes()
            for r in rows:
                line = raw[r * n:(r + 1) * n].decode("utf-32-le").rstrip()
                if line != st[r][k]:
                    st[r][k] = line
                    c.itemconfigure(self._items[r][k], text=line)
