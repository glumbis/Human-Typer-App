"""Small tk widgets in the terminal style: boxed panels, text buttons, toggles."""
import tkinter as tk

from . import theme as T


class Panel(tk.Frame):
    """Frame with a thin box border and a [ TITLE ] set into the top edge."""

    def __init__(self, master, title, fnt=None, color=None, bg=None, **kw):
        bg = bg or T.BG
        super().__init__(master, bg=bg, **kw)
        self.fnt = fnt or T.font(9)
        self.title = title
        self.color = color or T.LINE
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=bg)
        ls = self.fnt.metrics("linespace")
        self.body.pack(fill="both", expand=True, padx=(10, 10), pady=(ls + 6, 9))
        self.canvas.bind("<Configure>", self._draw)

    def set_title(self, title):
        self.title = title
        self._draw()

    def _draw(self, _e=None):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        ls = self.fnt.metrics("linespace")
        y = ls // 2 + 1
        x0, x1, y1 = 3, w - 4, h - 4
        txt = f"[ {self.title} ]"
        tw = self.fnt.measure(txt)
        c.create_line(x0, y, 12, y, fill=self.color)
        c.create_line(12 + tw + 8, y, x1, y, x1, y1, x0, y1, x0, y, fill=self.color)
        c.create_text(16, y, text=txt, anchor="w", font=self.fnt, fill=T.ICE)
        for cx, cy in ((x0, y), (x1, y), (x0, y1), (x1, y1)):   # corner pixels
            c.create_rectangle(cx - 1, cy - 1, cx + 1, cy + 1, fill=self.color, outline=self.color)


class TextButton(tk.Label):
    """[ LABEL ] that inverts (black on colour) on hover."""

    def __init__(self, master, text, command, fnt=None, color=T.CYAN, **kw):
        super().__init__(master, text=text, font=fnt or T.font(10, True), fg=color, bg=T.BG,
                         cursor="hand2", padx=2, **kw)
        self.command = command
        self.color = color
        self.default_bg = T.BG
        self.enabled = True
        self.bind("<Enter>", lambda e: self._paint(True))
        self.bind("<Leave>", lambda e: self._paint(False))
        self.bind("<ButtonRelease-1>", self._click)

    def _paint(self, hover):
        if not self.enabled:
            self.configure(fg=T.DIM, bg=self.default_bg, cursor="arrow")
        elif hover:
            self.configure(fg=T.BG, bg=self.color, cursor="hand2")
        else:
            self.configure(fg=self.color, bg=self.default_bg, cursor="hand2")

    def _click(self, e):
        if self.enabled and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self.command()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self._paint(False)


class Toggle(tk.Frame):
    """Row of clickable [OPTION] labels; the active one is cyan."""

    def __init__(self, master, options, value, command=None, fnt=None):
        super().__init__(master, bg=T.BG)
        self.fnt = fnt or T.font(10, True)
        self.command = command
        self.value = value
        self.labels = {}
        for opt in options:
            lb = tk.Label(self, text=f"[{opt.upper()}]", font=self.fnt, bg=T.BG, cursor="hand2", padx=1)
            lb.pack(side="left", padx=(0, 6))
            lb.bind("<Button-1>", lambda e, o=opt: self.set(o, True))
            lb.bind("<Enter>", lambda e, o=opt: self._paint(o, True))
            lb.bind("<Leave>", lambda e, o=opt: self._paint(o, False))
            self.labels[opt] = lb
        self._refresh()

    def _paint(self, opt, hover):
        lb = self.labels[opt]
        if opt == self.value:
            lb.configure(fg=T.CYAN, bg=T.BG3)
        else:
            lb.configure(fg=T.PINK if hover else T.DIM, bg=T.BG)

    def _refresh(self):
        for o in self.labels:
            self._paint(o, False)

    def get(self):
        return self.value

    def set(self, value, fire=False):
        if value not in self.labels:
            return
        changed = value != self.value
        self.value = value
        self._refresh()
        if fire and changed and self.command:
            self.command(value)


class AsciiView(tk.Text):
    """Read-only monospace grid painted from rows of (char, tag) cells; tags map to colours."""

    def __init__(self, master, fnt, cols, rows, tag_colors, bg=None, **kw):
        super().__init__(master, font=fnt, width=cols, height=rows, bg=bg or T.BG, fg=T.TEXT,
                         bd=0, highlightthickness=0, wrap="none", cursor="arrow", takefocus=0,
                         padx=0, pady=0, spacing1=0, spacing2=0, spacing3=0, state="disabled",
                         insertwidth=0, **kw)
        for name, col in tag_colors.items():
            self.tag_configure(name, foreground=col)
        self._last = None
        for ev in ("<Button-1>", "<B1-Motion>", "<Double-Button-1>", "<Triple-Button-1>"):
            self.bind(ev, lambda e: "break")

    def paint(self, grid):
        """grid: list of rows, each a list of (char, tag|None)."""
        if grid == self._last:
            return
        self._last = grid
        args = []
        for ri, row in enumerate(grid):
            run, tag = "", None
            for ch, tg in row:
                if tg == tag:
                    run += ch
                else:
                    if run:
                        args += [run, tag or "_"]
                    run, tag = ch, tg
            if run:
                args += [run, tag or "_"]
            if ri < len(grid) - 1:
                args += ["\n", "_"]
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.insert("end", *args)
        self.configure(state="disabled")


class AsciiCanvas(tk.Canvas):
    """Like AsciiView but drawn on a Canvas with the row pitch squeezed by a pixel, so block glyphs
    (█ ╗ ║ ...) join vertically instead of showing a seam between text lines."""

    def __init__(self, master, fnt, cols, rows, tag_colors, bg=None, squeeze=None):
        self.fnt = fnt
        self.cw = fnt.measure("M")
        ls = fnt.metrics("linespace")
        self.pitch = ls - (1 if squeeze is None else squeeze)
        self.cols, self.rows = cols, rows
        super().__init__(master, width=cols * self.cw, height=rows * self.pitch + (ls - self.pitch),
                         bg=bg or T.BG, bd=0, highlightthickness=0, takefocus=0)
        self.colors = dict(tag_colors)
        self._last = None

    def paint(self, grid):
        if grid == self._last:
            return
        self._last = grid
        self.delete("all")
        cw, pitch, fnt, cols = self.cw, self.pitch, self.fnt, self.colors
        for ri, row in enumerate(grid):
            y = ri * pitch
            ci, n = 0, len(row)
            while ci < n:
                tag = row[ci][1]
                cj = ci
                while cj < n and row[cj][1] == tag:
                    cj += 1
                if tag is not None:
                    run = "".join(c for c, _ in row[ci:cj])
                    if run.strip():
                        self.create_text(ci * cw, y, text=run, anchor="nw", font=fnt, fill=cols[tag])
                ci = cj


# --------------------------------------------------------------------------- ghost widgets
# Text widgets drawn as canvas items directly on the galaxy backdrop, so the animation shows through
# (tk widgets are opaque). They expose the same small API the app uses on the tk versions.
_gid = [0]


class _Ghost:
    def __init__(self, canvas):
        _gid[0] += 1
        self.canvas = canvas
        self.tag = f"gh{_gid[0]}"

    def bbox(self):
        return self.canvas.bbox(self.tag)

    def width(self):
        b = self.bbox()
        return (b[2] - b[0]) if b else 0

    def moveto(self, x, y):
        b = self.bbox()
        if b:
            self.canvas.move(self.tag, x - b[0], y - b[1])


class GLabel(_Ghost):
    def __init__(self, canvas, text, fnt, fg=None, anchor="nw"):
        super().__init__(canvas)
        self.fnt = fnt
        self.item = canvas.create_text(0, 0, text=text, font=fnt, fill=fg or T.TEXT, anchor=anchor,
                                       tags=(self.tag,))

    def configure(self, text=None, fg=None, **_):
        if text is not None:
            self.canvas.itemconfigure(self.item, text=text)
        if fg is not None:
            self.canvas.itemconfigure(self.item, fill=fg)


class GButton(GLabel):
    """[ LABEL ] that inverts on hover (same behaviour as TextButton)."""

    def __init__(self, canvas, text, command, fnt=None, color=T.CYAN):
        super().__init__(canvas, text, fnt or T.font(10, True), color)
        self.command, self.color, self.enabled = command, color, True
        self.rect = canvas.create_rectangle(0, 0, 0, 0, fill="", outline="", tags=(self.tag,))
        canvas.tag_lower(self.rect, self.item)
        self._fit()
        for it in (self.item, self.rect):
            canvas.tag_bind(it, "<Enter>", lambda e: self._paint(True))
            canvas.tag_bind(it, "<Leave>", lambda e: self._paint(False))
            canvas.tag_bind(it, "<ButtonRelease-1>", self._click)

    def _fit(self):
        b = self.canvas.bbox(self.item)
        if b:
            self.canvas.coords(self.rect, b[0] - 2, b[1], b[2] + 2, b[3])

    def configure(self, text=None, **kw):
        super().configure(text=text, **kw)
        self._fit()

    def _paint(self, hover):
        c = self.canvas
        if not self.enabled:
            c.itemconfigure(self.item, fill=T.DIM)
            c.itemconfigure(self.rect, fill="")
            c.configure(cursor="")
        elif hover:
            c.itemconfigure(self.item, fill=T.BG)
            c.itemconfigure(self.rect, fill=self.color)
            c.configure(cursor="hand2")
        else:
            c.itemconfigure(self.item, fill=self.color)
            c.itemconfigure(self.rect, fill="")
            c.configure(cursor="")

    def _click(self, e):
        if self.enabled:
            self.command()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self._paint(False)


class GToggle(_Ghost):
    """Row of [OPTION] labels on the canvas; the active one is cyan on a dim plate."""

    def __init__(self, canvas, options, value, command=None, fnt=None):
        super().__init__(canvas)
        self.fnt = fnt or T.font(10, True)
        self.command, self.value = command, value
        self.opts = {}
        x = 0
        for opt in options:
            txt = canvas.create_text(x + 2, 0, text=f"[{opt.upper()}]", font=self.fnt, anchor="nw",
                                     fill=T.DIM, tags=(self.tag,))
            b = canvas.bbox(txt)
            rect = canvas.create_rectangle(b[0] - 2, b[1], b[2] + 2, b[3], fill="", outline="",
                                           tags=(self.tag,))
            canvas.tag_lower(rect, txt)
            self.opts[opt] = (txt, rect)
            x = b[2] + 10
            for it in (txt, rect):
                canvas.tag_bind(it, "<Button-1>", lambda e, o=opt: self.set(o, True))
                canvas.tag_bind(it, "<Enter>", lambda e, o=opt: self._paint(o, True))
                canvas.tag_bind(it, "<Leave>", lambda e, o=opt: self._paint(o, False))
        self._refresh()

    def _paint(self, opt, hover):
        txt, rect = self.opts[opt]
        c = self.canvas
        if opt == self.value:
            c.itemconfigure(txt, fill=T.CYAN)
            c.itemconfigure(rect, fill=T.BG3)
        else:
            c.itemconfigure(txt, fill=T.PINK if hover else T.DIM)
            c.itemconfigure(rect, fill="")
        c.configure(cursor="hand2" if hover else "")

    def _refresh(self):
        for o in self.opts:
            self._paint(o, False)

    def get(self):
        return self.value

    def set(self, value, fire=False):
        if value not in self.opts:
            return
        changed = value != self.value
        self.value = value
        self._refresh()
        if fire and changed and self.command:
            self.command(value)


class GBox(_Ghost):
    """Panel border with a [ TITLE ] in the top edge, drawn on the backdrop canvas."""

    def __init__(self, canvas, title, fnt, color=None):
        super().__init__(canvas)
        self.title, self.fnt, self.color = title, fnt, color or T.LINE

    def draw(self, x0, y0, x1, y1):
        c = self.canvas
        c.delete(self.tag)
        ls = self.fnt.metrics("linespace")
        y = y0 + ls // 2 + 1
        txt = f"[ {self.title} ]"
        tw = self.fnt.measure(txt)
        t = (self.tag,)
        c.create_line(x0, y, x0 + 9, y, fill=self.color, tags=t)
        c.create_line(x0 + 9 + tw + 8, y, x1, y, x1, y1, x0, y1, x0, y, fill=self.color, tags=t)
        c.create_text(x0 + 13, y, text=txt, anchor="w", font=self.fnt, fill=T.ICE, tags=t)
        for cx, cy in ((x0, y), (x1, y), (x0, y1), (x1, y1)):
            c.create_rectangle(cx - 1, cy - 1, cx + 1, cy + 1, fill=self.color, outline=self.color, tags=t)


class CanvasArt:
    """Paints rows of (char, tag) cells onto a shared canvas at (x, y), redrawing only rows that
    changed. Same pitch squeeze as AsciiCanvas so block glyphs join vertically."""

    def __init__(self, canvas, fnt, x, y, tag_colors, name, rows, squeeze=1):
        self.canvas, self.fnt, self.x, self.y, self.name = canvas, fnt, x, y, name
        self.cw = fnt.measure("M")
        self.pitch = fnt.metrics("linespace") - squeeze
        self.colors = dict(tag_colors)
        self.rows = rows
        self._last = [None] * rows

    def height(self):
        return self.rows * self.pitch + 1

    def paint(self, grid):
        c, cw = self.canvas, self.cw
        for ri, row in enumerate(grid):
            key = tuple(row)
            if key == self._last[ri]:
                continue
            self._last[ri] = key
            rtag = f"{self.name}{ri}"
            c.delete(rtag)
            y = self.y + ri * self.pitch
            ci, n = 0, len(row)
            while ci < n:
                tag = row[ci][1]
                cj = ci
                while cj < n and row[cj][1] == tag:
                    cj += 1
                if tag is not None:
                    run = "".join(ch for ch, _ in row[ci:cj])
                    if run.strip():
                        c.create_text(self.x + ci * cw, y, text=run, anchor="nw", font=self.fnt,
                                      fill=self.colors[tag], tags=(self.name, rtag))
                ci = cj


class GWindow:
    """A tk widget embedded in the canvas (e.g. a slider); only supports moveto."""

    def __init__(self, canvas, win_item):
        self.canvas, self.item = canvas, win_item

    def moveto(self, x, y):
        self.canvas.coords(self.item, x, y)
