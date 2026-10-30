"""macOS SETUP window: walks the user through the two permissions Human Typer needs.

macOS never lets an app grant itself these, but it can ask the system to show its own prompt (which
also adds Human Typer to the list, so no "+" is needed) and open the exact Settings pane. Each row
shows a live status (polled every second on the Tk thread). When everything is granted the hotkeys
are switched on right away (no restart) and the window closes by itself; if macOS only applies the
permission after a relaunch, a [ RESTART HUMAN TYPER ] button appears.
"""
import tkinter as tk

from . import macutil
from . import theme as T
from .widgets import TextButton

OK_MARK, BAD_MARK = "✔ granted", "✗ missing"
INTRO = "macOS needs your OK for two things, once. Press GRANT, then turn on the switch next to Human Typer."
UPDATE_NOTE = ("Updated from an older version? If Human Typer is already in the list but off / not working, "
               "select it, click −, then press GRANT again.")
STATE_TEXT = {
    "missing": ("Waiting for macOS… (this window updates by itself)", T.DIM),
    "waiting": ("Permissions granted - switching the hotkeys on…", T.LAVENDER),
    "restart": ("macOS applies this permission after a restart: press RESTART HUMAN TYPER.", T.PINK),
    "ok": ("All set! Click into any text box and press fn+F8", T.CYAN),
}


class SetupWindow(tk.Toplevel):
    POLL_MS = 1000

    def __init__(self, master, hotkeys_ok, retry_hotkeys, on_relaunch, status_fn=None,
                 request_fns=None, open_pane=None, auto_close_ms=4000, on_closed=None):
        super().__init__(master, bg=T.BG)
        self.title("HUMAN TYPER // SETUP")
        self.resizable(False, False)
        self.configure(highlightthickness=1, highlightbackground=T.VIOLET)
        self.hotkeys_ok = hotkeys_ok
        self.retry_hotkeys = retry_hotkeys
        self.on_relaunch = on_relaunch
        self.status_fn = status_fn or macutil.permission_status
        self.request_fns = request_fns or {"accessibility": macutil.request_accessibility,
                                           "input": macutil.request_input_monitoring}
        self.open_pane = open_pane or macutil.open_settings
        self.auto_close_ms = auto_close_ms
        self.on_closed = on_closed
        self.state = None
        self.status = {}
        self.tap_failures = 0
        self._poll_id = self._close_id = None
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.poll()

    # ---- layout --------------------------------------------------------
    def _build(self):
        f9, f10, f10b = T.font(9), T.font(10), T.font(10, True)
        wrap = 560
        tk.Label(self, text="[ SETUP // PERMISSIONS ]", font=T.font(11, True), fg=T.ICE, bg=T.BG,
                 anchor="w").pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(self, text=INTRO, font=f9, fg=T.TEXT, bg=T.BG, justify="left", anchor="w",
                 wraplength=wrap).pack(fill="x", padx=16, pady=(0, 10))
        self.rows = {}
        for key, title, why, pane in macutil.PERMISSIONS:
            row = tk.Frame(self, bg=T.PANEL, highlightthickness=1, highlightbackground=T.LINE)
            row.pack(fill="x", padx=16, pady=4)
            top = tk.Frame(row, bg=T.PANEL)
            top.pack(fill="x", padx=10, pady=(6, 0))
            tk.Label(top, text=title, font=f10b, fg=T.TEXT, bg=T.PANEL).pack(side="left")
            btn = TextButton(top, "[ GRANT ]", lambda k=key, p=pane: self.grant(k, p), fnt=f10b, color=T.PINK)
            btn.default_bg = T.PANEL
            btn.configure(bg=T.PANEL)
            btn.pack(side="right")
            st = tk.Label(top, text="…", font=f10b, fg=T.DIM, bg=T.PANEL, width=11, anchor="w")
            st.pack(side="right", padx=(0, 12))
            tk.Label(row, text=f"{why}. Turn on the switch next to Human Typer.", font=f9, fg=T.DIM,
                     bg=T.PANEL, anchor="w").pack(fill="x", padx=10, pady=(0, 6))
            self.rows[key] = (st, btn)
        tk.Label(self, text=UPDATE_NOTE, font=f9, fg=T.LAVENDER, bg=T.BG, justify="left", anchor="w",
                 wraplength=wrap).pack(fill="x", padx=16, pady=(8, 6))
        self.msg = tk.Label(self, text="", font=f10b, fg=T.DIM, bg=T.BG, anchor="w", wraplength=wrap,
                            justify="left")
        self.msg.pack(fill="x", padx=16, pady=(4, 6))
        bar = tk.Frame(self, bg=T.BG)
        bar.pack(fill="x", padx=16, pady=(0, 14))
        self.b_done = TextButton(bar, "[ DONE ]", self.close, fnt=f10b, color=T.CYAN)
        self.b_done.pack(side="right")
        self.b_restart = TextButton(bar, "[ RESTART HUMAN TYPER ]", self.on_relaunch, fnt=f10b, color=T.PINK)
        self._restart_shown = False
        self.update_idletasks()
        try:                                       # centre over the main window
            m = self.master
            x = m.winfo_rootx() + max(0, (m.winfo_width() - self.winfo_reqwidth()) // 2)
            y = m.winfo_rooty() + max(0, (m.winfo_height() - self.winfo_reqheight()) // 3)
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    # ---- actions -------------------------------------------------------
    def grant(self, key, pane):
        """System prompt (also lists the app) + the exact Settings pane."""
        try:
            self.request_fns[key]()
        except Exception:
            pass
        self.open_pane(pane)
        self.after(300, self.poll)

    def close(self):
        for aid in (self._poll_id, self._close_id):
            if aid:
                try:
                    self.after_cancel(aid)
                except Exception:
                    pass
        self._poll_id = self._close_id = None
        try:
            self.destroy()
        except Exception:
            pass
        if self.on_closed:
            self.on_closed()

    # ---- live status ---------------------------------------------------
    def evaluate(self):
        """One poll: read the permissions, (re)try the hotkeys when they are all granted, decide."""
        self.status = dict(self.status_fn())
        ok = bool(self.hotkeys_ok())
        if not ok and not any(v is False for v in self.status.values()):
            self.retry_hotkeys()
            ok = bool(self.hotkeys_ok())
            self.tap_failures = 0 if ok else self.tap_failures + 1
        self.state = macutil.setup_state(self.status, ok, self.tap_failures)
        return self.state

    def poll(self):
        if self._poll_id:
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                pass
        self._poll_id = None
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        state = self.evaluate()
        for key, (st, btn) in self.rows.items():
            v = self.status.get(key)
            st.configure(text=BAD_MARK if v is False else OK_MARK, fg=T.ERR if v is False else T.CYAN)
            btn.set_enabled(v is False)
        text, col = STATE_TEXT[state]
        self.msg.configure(text=text, fg=col)
        if (state == "restart") != self._restart_shown:
            self._restart_shown = state == "restart"
            if self._restart_shown:
                self.b_restart.pack(side="left")
            else:
                self.b_restart.pack_forget()
        if state == "ok":
            if self._close_id is None and self.auto_close_ms is not None:
                self._close_id = self.after(self.auto_close_ms, self.close)
        elif self._close_id is not None:
            self.after_cancel(self._close_id)
            self._close_id = None
        self._poll_id = self.after(self.POLL_MS, self.poll)

    def report(self):
        """For --e2e / --diag."""
        return {"state": self.state, "status": dict(self.status), "tap_failures": self.tap_failures,
                "restart_button": self._restart_shown, "closing": self._close_id is not None}
