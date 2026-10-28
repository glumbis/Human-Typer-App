"""Small tk widgets in the terminal style: boxed panels, text buttons, toggles."""
import tkinter as tk

from . import theme as T


class Panel(tk.Frame):
    """Frame with a thin box border and a [ TITLE ] set into the top edge."""

    def __init__(self, master, title, fnt=None, color=None, **kw):
        super().__init__(master, bg=T.BG, **kw)
        self.fnt = fnt or T.font(9)
        self.title = title
        self.color = color or T.LINE
        self.canvas = tk.Canvas(self, bg=T.BG, highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=T.BG)
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
        c.create_text(16, y, text=txt, anchor="w", font=self.fnt, fill=T.GREEN)
        for cx, cy in ((x0, y), (x1, y), (x0, y1), (x1, y1)):   # corner pixels
            c.create_rectangle(cx - 1, cy - 1, cx + 1, cy + 1, fill=self.color, outline=self.color)


class TextButton(tk.Label):
    """[ LABEL ] that inverts (black on colour) on hover."""

    def __init__(self, master, text, command, fnt=None, color=T.GREEN, **kw):
        super().__init__(master, text=text, font=fnt or T.font(10, True), fg=color, bg=T.BG,
                         cursor="hand2", padx=2, **kw)
        self.command = command
        self.color = color
        self.enabled = True
        self.bind("<Enter>", lambda e: self._paint(True))
        self.bind("<Leave>", lambda e: self._paint(False))
        self.bind("<ButtonRelease-1>", self._click)

    def _paint(self, hover):
        if not self.enabled:
            self.configure(fg=T.DIM, bg=T.BG, cursor="arrow")
        elif hover:
            self.configure(fg=T.BG, bg=self.color, cursor="hand2")
        else:
            self.configure(fg=self.color, bg=T.BG, cursor="hand2")

    def _click(self, e):
        if self.enabled and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self.command()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self._paint(False)


class Toggle(tk.Frame):
    """Row of clickable [OPTION] labels; the active one is green."""

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
            lb.configure(fg=T.GREEN, bg=T.BG2)
        else:
            lb.configure(fg=T.AMBER if hover else T.DIM, bg=T.BG)

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
