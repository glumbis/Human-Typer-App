"""Always-on-top mini stats window, borderless and draggable by its title strip. Windows: never
activates (WS_EX_NOACTIVATE). macOS: FocusReturn gives the keyboard focus back to the app the user
was typing in after a click on the overlay."""
import sys
import time
import tkinter as tk

from .. import plat
from . import macutil
from . import theme as T
from . import winutil
from .stats import fmt_time
from .widgets import TextButton

W, H = 276, 120
STATE_COLORS = {"READY": T.ICE, "TYPING": T.CYAN, "REVISING": T.VIOLET, "PAUSED": T.PINK,
                "DONE": T.CYAN, "STOPPED": T.ERR}


class StatsOverlay(tk.Toplevel):
    def __init__(self, master, pos=None, on_start=None, on_pause=None, on_stop=None,
                 on_home=None, on_close=None, on_moved=None, on_press=None, on_release=None,
                 alpha=0.95):
        super().__init__(master, bg=T.LINE)
        self.cb = dict(start=on_start, pause=on_pause, stop=on_stop, home=on_home,
                       close=on_close, moved=on_moved, press=on_press, release=on_release)
        self.withdraw()
        # Borderless everywhere. Windows: overrideredirect + WS_EX_NOACTIVATE (_style). macOS: plain
        # overrideredirect + -topmost. NOT a MacWindowStyle "floating" panel: Tk on Aqua drops every
        # click on such a panel while another app is active, with or without noActivates, so the
        # buttons did nothing in v1.4.3 (proven by tools/mac_e2e.sh, which clicks them for real).
        self.overrideredirect(True)
        # extra safety net on macOS: remember the last other frontmost app, re-activate it after clicks
        self.focus_ret = macutil.FocusReturn() if plat.IS_MAC else None
        self._focus_id = None
        try:
            self.attributes("-alpha", alpha)
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.shown = False
        self._fired = None
        self.exstyle = 0
        self._flash = ("", 0.0)
        self._drag = None
        self._lift_id = None
        self._build()
        self.geometry(self._place(pos))
        self.bind("<Map>", lambda e: self.after_idle(self._style))
        if plat.IS_MAC:
            # A click on the overlay activates this app for a moment (macOS), so keys typed meanwhile
            # would be lost: tell the app on mouse-down (it holds the typing) and after the click
            # (bindings on the toplevel run after the button's own <ButtonRelease-1> handler).
            self.bind("<ButtonPress-1>", lambda e: self._fire("press"), add="+")
            self.bind("<ButtonRelease-1>", self._on_release, add="+")

    # ---- layout --------------------------------------------------------
    def _build(self):
        f8, f9, f9n = T.font(8), T.font(9, True), T.font(9)
        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        title = tk.Frame(body, bg=T.BG2, cursor="fleur")
        title.pack(fill="x")
        self.deco_lbl = tk.Label(title, text="::", font=f8, fg=T.VIOLET, bg=T.BG2,
                                 cursor="fleur", padx=0)
        self.deco_lbl.pack(side="left", padx=(5, 0))
        self.title_lbl = tk.Label(title, text="HUMAN TYPER", font=T.font(8, True),
                                  fg=T.LAVENDER, bg=T.BG2, cursor="fleur", padx=4)
        self.title_lbl.pack(side="left")
        self.b_close = TextButton(title, "[x]", lambda: self._fire("close"), fnt=f8, color=T.ERR)
        self.b_close.pack(side="right", padx=(0, 2))
        self.b_home = TextButton(title, "[⌂]", lambda: self._fire("home"), fnt=f8, color=T.ICE)
        self.b_home.pack(side="right")
        for b in (self.b_home, self.b_close):
            b.default_bg = T.BG2
            b.configure(bg=T.BG2)
        for w in (title, self.title_lbl, self.deco_lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
        self.glow = tk.Canvas(body, height=2, bg=T.BG, bd=0, highlightthickness=0)
        self.glow.pack(fill="x")
        self.glow.bind("<Configure>", self._draw_glow)

        info = tk.Frame(body, bg=T.BG)
        info.pack(fill="both", expand=True, padx=7, pady=(4, 0))
        barrow = tk.Frame(info, bg=T.BG)
        barrow.pack(fill="x")
        self.l_state = tk.Label(barrow, text="", font=f9, fg=T.CYAN, bg=T.BG, anchor="w", width=8)
        self.l_state.pack(side="left")
        self.l_pct = tk.Label(barrow, text="", font=f9, fg=T.ICE, bg=T.BG, anchor="e", width=4)
        self.l_pct.pack(side="right")
        self.bar = tk.Canvas(barrow, height=12, bg=T.BG, bd=0, highlightthickness=0)
        self.bar.pack(side="left", fill="x", expand=True, padx=(2, 8))
        self._bar_state = (0, T.CYAN)
        self.bar.bind("<Configure>", lambda e: self._draw_bar(*self._bar_state))
        self.l_time = tk.Label(info, text="", font=f8, fg=T.TEXT, bg=T.BG, anchor="w")
        self.l_time.pack(fill="x", pady=(2, 0))
        self.l_cnt = tk.Label(info, text="", font=f8, fg=T.DIM, bg=T.BG, anchor="w")
        self.l_cnt.pack(fill="x")

        btns = tk.Frame(body, bg=T.BG)
        btns.pack(fill="x", padx=5, pady=(3, 5))
        self.b_start = TextButton(btns, "[▶ START]", lambda: self._fire("start"), fnt=f8, color=T.CYAN)
        self.b_pause = TextButton(btns, "[‖ PAUSE]", lambda: self._fire("pause"), fnt=f8, color=T.PINK)
        self.b_stop = TextButton(btns, "[■ STOP]", lambda: self._fire("stop"), fnt=f8, color=T.ERR)
        for b in (self.b_start, self.b_pause, self.b_stop):
            b.pack(side="left", padx=(0, 4))
        self.b_pause.set_enabled(False)
        self.b_stop.set_enabled(False)
        self.refresh(dict(state="READY"))

    def _draw_glow(self, _e=None):
        c = self.glow
        c.delete("all")
        w = max(1, c.winfo_width())
        n = 40
        for i in range(n):
            c.create_rectangle(i * w / n, 0, (i + 1) * w / n + 1, 2, width=0,
                               fill=T.gradient(i / (n - 1.0), 0.85))

    def _draw_bar(self, pct, color):
        c = self.bar
        self._bar_state = (pct, color)
        c.delete("all")
        w, h = max(40, c.winfo_width()), 12
        n = 24
        seg = w / float(n)
        done = int(n * pct / 100.0 + 0.5)
        for i in range(n):
            fill = T.gradient(i / (n - 1.0)) if i < done else "#221a58"
            c.create_rectangle(i * seg + 1, 2, (i + 1) * seg - 1, h - 2, width=0, fill=fill)

    def _fire(self, name):
        if name not in ("press", "release"):
            self._fired = name
        fn = self.cb.get(name)
        if fn:
            fn()
        if name in ("start", "pause", "stop"):
            self.return_focus()

    def _on_release(self, _e=None):
        """macOS: end of any click on the overlay. A drag or a click on the stats gives the focus back
        too ([⌂] deliberately does not); then the app may resume typing held by the mouse-down."""
        fired, self._fired = self._fired, None
        if fired in (None, "moved"):
            self.return_focus()
        self._fire("release")

    # ---- macOS focus return -------------------------------------------
    def can_return_focus(self):
        return bool(self.focus_ret and self.focus_ret.available)

    def return_focus(self):
        """Hand keyboard focus back to the app the user was in before clicking us (macOS only).
        Tried a few times: the click's own activation of this app may land after the first try
        (restore() is a no-op while another app is in front)."""
        if self.can_return_focus():
            for ms in (30, 150, 400, 800):
                self.after(ms, self.focus_ret.restore)

    def _poll_focus(self):
        self._focus_id = None
        if not self.shown or not self.can_return_focus():
            return
        self.focus_ret.poll()
        self._focus_id = self.after(400, self._poll_focus)

    # ---- placement / drag ---------------------------------------------
    def _place(self, pos):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        try:
            x, y = int(pos[0]), int(pos[1])
            if not (-W + 40 < x < sw - 40 and 0 <= y < sh - 30):
                raise ValueError
        except (TypeError, ValueError, IndexError):
            x, y = sw - W - 24, 24
        return f"{W}x{H}+{x}+{y}"

    def position(self):
        return self.winfo_x(), self.winfo_y()

    def _drag_start(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, _e):
        if self._drag:
            self._drag = None
            self._fire("moved")

    # ---- visibility / style -------------------------------------------
    def show(self):
        if self.shown:
            return
        self.shown = True
        self.deiconify()
        self.update_idletasks()
        self._style()
        self._keep_on_top()
        if self.can_return_focus() and self._focus_id is None:
            self._poll_focus()

    def hide(self):
        self.shown = False
        if self._lift_id:
            self.after_cancel(self._lift_id)
            self._lift_id = None
        if self._focus_id:
            self.after_cancel(self._focus_id)
            self._focus_id = None
        self.withdraw()

    def _style(self):
        if sys.platform == "win32" and self.winfo_exists():
            self.exstyle = winutil.make_noactivate(self)

    def _keep_on_top(self):
        if not self.shown:
            return
        winutil.raise_topmost(self)
        self._lift_id = self.after(2500, self._keep_on_top)

    def has_noactivate(self):
        return bool(self.exstyle & winutil.WS_EX_NOACTIVATE)

    # ---- content -------------------------------------------------------
    def flash(self, text, secs=2.0):
        self._flash = (text, time.time() + secs)

    def refresh(self, d):
        """d keys: state, pct, wpm, elapsed, eta, typed, total, typos, revs, active, typing, paused."""
        state = d.get("state", "READY")
        if self._flash[0] and time.time() < self._flash[1]:
            state = self._flash[0]
        pct = int(d.get("pct", 0))
        color = STATE_COLORS.get(state, T.PINK)
        self.l_state.configure(text=state, fg=color)
        self._draw_bar(pct, color)
        self.l_pct.configure(text=f"{pct}%")
        wpm = int(round(d.get("wpm") or 0))
        self.l_time.configure(
            text=f"WPM {wpm:>3}  {fmt_time(d.get('elapsed') or 0)} / ETA {fmt_time(d.get('eta'))}")
        hint = (plat.IS_MAC and not self.can_return_focus() and state.startswith("T-"))
        hk_off = plat.IS_MAC and d.get("hotkeys") is False and state == "READY"
        self.l_cnt.configure(
            text="click back into your target app" if hint else
                 "fn+F8 OFF: allow Input Monitoring" if hk_off else
                 f"CHR {d.get('typed', 0)}/{d.get('total', 0)}  TYPO {d.get('typos', 0)}  "
                 f"REV {d.get('revs', '0/?')}")
        active = bool(d.get("active"))
        self.b_start.set_enabled(not active)
        self.b_stop.set_enabled(active)
        self.b_pause.set_enabled(bool(d.get("typing")))
        self.b_pause.configure(text="[▶ RESUME]" if d.get("paused") else "[‖ PAUSE]")

    def destroy(self):
        for aid in (self._lift_id, self._focus_id):
            if aid:
                try:
                    self.after_cancel(aid)
                except Exception:
                    pass
        super().destroy()
