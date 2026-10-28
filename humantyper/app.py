"""customtkinter GUI for Human Typer: retro ASCII terminal skin."""
import os
import queue
import time
import tkinter as tk

import customtkinter as ctk

from . import __version__
from . import config
from .backends import PynputBackend
from .engine import Settings, TypingEngine
from .hotkeys import GlobalHotkeys
from .parser import parse
from .ui import art
from .ui import theme as T
from .ui.widgets import AsciiCanvas, AsciiView, Panel, TextButton, Toggle
from .ui.overlay import StatsOverlay
from .ui.stats import Stats

READY = "READY — click into any app and press F8"
MODES = {"RANDOM": "random", "END": "end"}
LOCATE = ("auto", "clipboard", "count")
BAR_W = 30
STREAM_W = 26


class HumanTyperApp(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        super().__init__()
        T.init_fonts(self)
        self.title("HUMAN TYPER")
        self.geometry("760x650")
        self.minsize(720, 620)
        self.configure(fg_color=T.BG)
        self._set_icon()

        self.cfg = config.load()
        self.events: "queue.Queue" = queue.Queue()
        self.st = "idle"            # idle | starting | countdown | typing
        self.engine = None
        self.backend = None
        self._after_id = None
        self._anim_id = None
        self._pct = 0
        self._kind = "typing"
        self._phase = 0
        self._hue = 0.0
        self._cursor_on = True
        self._status_color = T.ACCENT
        self._stream = ""
        self._stream_dirty = False
        self._t0 = 0.0
        self._nchars = 0
        self._ntypos = 0
        self._last_epoch = 0
        self._face = art.Face()
        self.stats = Stats()
        self.overlay = None
        self._ov_dirty = True
        self._ov_last = 0.0
        self._ov_result = ""        # "DONE" / "STOPPED" shown until the next run
        self._countdown_n = 0
        self._face_p = 0.0

        self._build()
        self.attributes("-topmost", bool(self.cfg["always_on_top"]))
        self._build_overlay()
        self._draw_banner()
        self._draw_face()
        self._set_status(READY)

        self.hotkeys = GlobalHotkeys(lambda: self.events.put(("hotkey", "f8")),
                                     lambda: self.events.put(("hotkey", "f9")))
        try:
            self.hotkeys.start()
        except Exception as e:  # pragma: no cover
            self._set_status(f"Hotkeys unavailable: {e}", T.ERR)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(40, self._poll)
        self.after(530, self._blink)

    def _set_icon(self):
        path = T.resource_path(os.path.join("assets", "icon.ico"))
        if not os.path.exists(path):
            return
        try:
            self.iconbitmap(path)
            # customtkinter installs its own icon shortly after start-up; set ours again after that
            self.after(300, lambda: self.iconbitmap(path))
        except Exception:
            pass

    # ---- UI -----------------------------------------------------------
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0)
        self.rowconfigure(1, weight=1)
        f9, f10 = T.font(9), T.font(10)

        # banner -------------------------------------------------------
        top = tk.Frame(self, bg=T.BG)
        top.grid(row=0, column=0, columnspan=2, sticky="we", padx=14, pady=(10, 2))
        glyphs = T.unmeasured_ok(self, T.font(8), list(".·˙'`,+*✦⋆"))
        self.banner = art.Banner("HUMAN TYPER", glyph_ok=glyphs)
        self.banner_view = AsciiCanvas(top, T.font(8), self.banner.cols, self.banner.rows,
                                     art.Banner.tag_colors())
        self.banner_view.pack(anchor="w")
        tk.Label(top, text=f"// human-like keystroke engine  ::  v{__version__}  ::  F8 start/stop  F9 pause //",
                 font=f9, fg=T.DIM, bg=T.BG, anchor="w").pack(anchor="w", pady=(2, 0))

        # left column: input + menu ------------------------------------
        left = tk.Frame(self, bg=T.BG)
        left.grid(row=1, column=0, sticky="nsew", padx=(10, 4), pady=(6, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        inp = Panel(left, "PASTE TEXT // [first][revised] = later revision", f9)
        inp.grid(row=0, column=0, sticky="nsew")
        self.textbox = ctk.CTkTextbox(
            inp.body, wrap="word", font=ctk.CTkFont(family=T.font().actual("family"), size=13),
            fg_color=T.INPUT_BG, text_color=T.TEXT, border_width=1, border_color=T.LINE,
            corner_radius=0, scrollbar_button_color=T.LINE, scrollbar_button_hover_color=T.VIOLET,
            height=120)
        self.textbox.pack(fill="both", expand=True)
        self.textbox._textbox.configure(insertbackground=T.CYAN, insertwidth=2,
                                        selectbackground=T.VIOLET, selectforeground=T.WHITE)
        if self.cfg.get("last_text"):
            self.textbox.insert("1.0", self.cfg["last_text"])

        menu = Panel(left, "COMMAND MENU", f9)
        menu.grid(row=1, column=0, sticky="we", pady=(8, 0))
        mb = menu.body
        mb.columnconfigure(1, weight=1)

        row0 = tk.Frame(mb, bg=T.BG)
        row0.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        tk.Label(row0, text="1)", font=f10, fg=T.DIM, bg=T.BG).pack(side="left")
        self.paste_btn = TextButton(row0, "[ PASTE ]", self._paste)
        self.paste_btn.pack(side="left", padx=(2, 14))
        tk.Label(row0, text="2)", font=f10, fg=T.DIM, bg=T.BG).pack(side="left")
        self.clear_btn = TextButton(row0, "[ CLEAR ]", self._clear)
        self.clear_btn.pack(side="left", padx=2)

        self.wpm_var = ctk.DoubleVar(value=float(self.cfg["wpm"]))
        self.typo_var = ctk.DoubleVar(value=float(self.cfg["typo_rate"]))
        self.wpm_lbl = self._slider(mb, 1, "3) WPM", self.wpm_var, 10, 200, 190, self._wpm_text)
        self.typo_lbl = self._slider(mb, 2, "4) TYPO RATE", self.typo_var, 0, 10, 100, self._typo_text)

        cur = next((k for k, v in MODES.items() if v == self.cfg["revise_mode"]), "RANDOM")
        self.mode_btn = Toggle(mb, list(MODES), cur)
        self._menu_row(mb, 3, "5) REVISE", self.mode_btn)

        loc = self.cfg.get("locate_mode", "auto")
        self.locate_btn = Toggle(mb, LOCATE, loc if loc in LOCATE else "auto")
        self._menu_row(mb, 4, "6) LOCATE", self.locate_btn)

        self.top_btn = Toggle(mb, ["yes", "no"], "yes" if self.cfg["always_on_top"] else "no",
                              command=lambda v: self.attributes("-topmost", v == "yes"))
        self._menu_row(mb, 5, "7) ALWAYS ON TOP", self.top_btn)

        self.overlay_btn = Toggle(mb, ["yes", "no"], "yes" if self.cfg.get("overlay", True) else "no",
                                  command=lambda v: self._set_overlay(v == "yes"))
        self._menu_row(mb, 6, "8) OVERLAY", self.overlay_btn)

        # right column: face + log -------------------------------------
        right = tk.Frame(self, bg=T.BG)
        right.grid(row=1, column=1, sticky="nsew", padx=(4, 10), pady=(6, 0))
        right.rowconfigure(1, weight=1)

        fp = Panel(right, "SUBJECT_00", f9)
        fp.grid(row=0, column=0, sticky="we")
        self.face_view = AsciiView(fp.body, T.font(6), art.FW, art.FH, art.Face.tag_colors())
        self.face_view.pack()
        self.face_cap = tk.Label(fp.body, text="", font=T.font(8), fg=T.DIM, bg=T.BG, anchor="w")
        self.face_cap.pack(fill="x", pady=(3, 0))

        lp = Panel(right, "LOG", f9)
        lp.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.log = tk.Text(lp.body, font=T.font(8), bg=T.BG, fg=T.TEXT, bd=0, highlightthickness=0,
                           width=30, height=5, wrap="none", cursor="arrow", takefocus=0,
                           padx=0, pady=0, state="disabled")
        self.log.pack(fill="both", expand=True)
        for name, col in (("dim", T.DIM), ("ok", T.CYAN), ("warn", T.PINK), ("err", T.ERR),
                          ("rev", T.LAVENDER), ("hi", T.ICE)):
            self.log.tag_configure(name, foreground=col)
        self.stream_lbl = tk.Label(lp.body, text="", font=T.font(8, True), fg=T.ACCENT, bg=T.BG,
                                   anchor="w")
        self.stream_lbl.pack(fill="x", pady=(4, 0))

        # bottom: status + buttons -------------------------------------
        sp = Panel(self, "STATUS", f9)
        sp.grid(row=2, column=0, columnspan=2, sticky="we", padx=10, pady=(8, 10))
        sb = sp.body
        sb.columnconfigure(0, weight=1)
        line = tk.Frame(sb, bg=T.BG)
        line.grid(row=0, column=0, columnspan=2, sticky="we")
        tk.Label(line, text="STATUS >", font=T.font(11, True), fg=T.DIM, bg=T.BG).pack(side="left")
        self.status = tk.Label(line, text="", font=T.font(11, True), fg=T.ACCENT, bg=T.BG)
        self.status.pack(side="left", padx=(6, 0))
        self.cursor = tk.Label(line, text="█", font=T.font(11, True), fg=T.ACCENT, bg=T.BG)
        self.cursor.pack(side="left")
        self.bar = tk.Label(sb, text="", font=T.font(10), fg=T.ACCENT, bg=T.BG, anchor="w")
        self.bar.grid(row=1, column=0, sticky="w", pady=(4, 6))
        btns = tk.Frame(sb, bg=T.BG)
        btns.grid(row=1, column=1, sticky="e", pady=(4, 6))
        self.start_btn = TextButton(btns, "[ F8 START ]", self._start_button, fnt=T.font(11, True))
        self.start_btn.pack(side="left", padx=4)
        self.pause_btn = TextButton(btns, "[ F9 PAUSE ]", self._toggle_pause, fnt=T.font(11, True),
                                    color=T.WARN)
        self.pause_btn.pack(side="left", padx=4)
        self.stop_btn = TextButton(btns, "[ STOP ]", self._stop, fnt=T.font(11, True), color=T.ERR)
        self.stop_btn.pack(side="left", padx=4)
        self.pause_btn.set_enabled(False)
        self.stop_btn.set_enabled(False)
        self._set_progress(0)
        self._wpm_text(self.wpm_var.get())
        self._typo_text(self.typo_var.get())

    def _menu_row(self, parent, r, name, widget):
        tk.Label(parent, text=self._dots(name), font=T.font(10),
                 fg=T.TEXT, bg=T.BG, anchor="w").grid(row=r, column=0, sticky="w", pady=3)
        widget.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)

    @staticmethod
    def _dots(name, width=20):
        return (name + " ").ljust(width, ".") + " "

    def _slider(self, parent, r, name, var, lo, hi, steps, fmt):
        tk.Label(parent, text=self._dots(name), font=T.font(10), fg=T.TEXT, bg=T.BG,
                 anchor="w").grid(row=r, column=0, sticky="w", pady=3)
        s = ctk.CTkSlider(parent, from_=lo, to=hi, number_of_steps=steps, variable=var, command=fmt,
                          height=14, corner_radius=0, button_corner_radius=0, button_length=6,
                          fg_color=T.BG3, progress_color=T.VIOLET, button_color=T.CYAN,
                          button_hover_color=T.PINK, bg_color=T.BG, border_width=0)
        s.grid(row=r, column=1, sticky="we", padx=(0, 10), pady=3)
        lbl = tk.Label(parent, font=T.font(10), fg=T.ACCENT, bg=T.BG, anchor="w", width=20)
        lbl.grid(row=r, column=2, sticky="w", pady=3)
        return lbl

    def _wpm_text(self, v):
        v = int(round(float(v)))
        self.wpm_lbl.configure(text=f"{T.ascii_bar(v, 10, 200, 10)} {v}")

    def _typo_text(self, v):
        v = float(v)
        val = "OFF" if v < 0.05 else f"{v:.1f}%"
        self.typo_lbl.configure(text=f"{T.ascii_bar(v, 0, 10, 10)} {val}")

    # ---- overlay ------------------------------------------------------
    def _build_overlay(self):
        pos = (self.cfg.get("overlay_x"), self.cfg.get("overlay_y"))
        self.overlay = StatsOverlay(
            self, pos=pos, on_start=self._overlay_start, on_pause=self._toggle_pause, on_stop=self._stop,
            on_home=self._raise_main, on_close=lambda: self._set_overlay(False), on_moved=self._save_cfg)
        if self.cfg.get("overlay", True):
            self.overlay.show()

    def _set_overlay(self, on):
        self.overlay_btn.set("yes" if on else "no")
        if on:
            self.overlay.show()
            self._ov_dirty = True
        else:
            self.overlay.hide()
        self._save_cfg()

    def _raise_main(self):
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _overlay_start(self):
        """Overlay START: focus stays in the target app, so only a short delay is needed."""
        if self.st == "idle":
            self._hotkey_start(delay=300)

    def _overlay_state(self):
        if self.st == "idle":
            return self._ov_result or "READY"
        if self.st == "countdown":
            return f"T-{self._countdown_n}"
        if self.st == "starting":
            return "ARMED"
        if self.engine and self.engine.paused:
            return "PAUSED"
        return "REVISING" if self._kind == "revising" else "TYPING"

    def _sync_overlay(self, force=False):
        ov = self.overlay
        if ov is None or not ov.shown:
            return
        now = time.time()
        if not (force or self._ov_dirty or now - self._ov_last >= (0.25 if self.st != "idle" else 1.0)):
            return
        self._ov_dirty, self._ov_last = False, now
        stt = self.stats
        active = self.st != "idle"
        ov.refresh(dict(
            state=self._overlay_state(), pct=self._pct, wpm=stt.wpm(), elapsed=stt.elapsed(),
            eta=stt.eta() if active else None, typed=stt.total if self._ov_result == "DONE" else stt.net_chars(), total=stt.total,
            typos=stt.typos, revs=stt.revisions_text(), active=active, typing=self.st == "typing",
            paused=bool(self.engine and self.engine.paused)))

    # ---- art ----------------------------------------------------------
    def _draw_banner(self):
        self.banner_view.paint(self.banner.cells(self._phase, self._hue))

    def _draw_face(self):
        self.face_view.paint(self._face.cells(self._face_p, self._phase, glitch=self.st != "idle"))
        pct = int(self._face_p * 100)
        self.face_cap.configure(text=f"COHERENCE {100 - pct:3d}%  STARDUST {pct:3d}%")

    def _animating(self):
        return self.st != "idle" and not (self.engine and self.engine.paused)

    def _kick_anim(self):
        if self._anim_id is None:
            self._anim_id = self.after(100, self._anim)

    def _anim(self):
        self._anim_id = None
        if not self._animating():
            return
        self._phase += 1
        self._hue = (self._hue + 0.01) % 2.0
        self._draw_banner()
        if self._phase % 2 == 0:
            self._draw_face()
        self._kick_anim()

    def _blink(self):
        self._cursor_on = not self._cursor_on
        self.cursor.configure(fg=self._status_color if self._cursor_on else T.BG)
        self.after(530, self._blink)

    # ---- log / stream -------------------------------------------------
    def _log(self, text, tag=None):
        if tag is None:
            up = text.upper()
            if "TYPO" in up or "FIX" in up or "ERROR" in up:
                tag = "warn"
            elif "REV" in up:
                tag = "rev"
            elif "✓" in text or "OK" in up:
                tag = "ok"
            elif "✗" in text or "FAIL" in up:
                tag = "err"
        stamp = time.strftime("%M:%S", time.gmtime(max(0, time.time() - self._t0))) if self._t0 else "--:--"
        self.log.configure(state="normal")
        self.log.insert("end", stamp + " ", "dim")
        self.log.insert("end", text + "\n", tag or ())
        n = int(self.log.index("end-1c").split(".")[0])
        if n > 300:
            self.log.delete("1.0", f"{n - 300}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _push_char(self, ch):
        if ch == "\b":
            self._stream = self._stream[:-1]
            self._ntypos += 1
        else:
            self._stream = (self._stream + ("↵" if ch == "\n" else "⇥" if ch == "\t" else ch))[-120:]
            self._nchars += 1
        self._stream_dirty = True

    def _refresh_stream(self):
        if self._stream_dirty:
            self._stream_dirty = False
            tail = self._stream[-STREAM_W:]
            self.stream_lbl.configure(text=f"> typing: {tail}_")

    def _epoch(self):
        el = max(1e-3, time.time() - self._t0)
        wpm = self._nchars / 5.0 / (el / 60.0)
        err = self._ntypos / max(1, self._nchars)
        self._last_epoch += 1
        self._log(f"EPOCH: {self._last_epoch} // AVG WPM: {wpm:.1f}", "hi")
        self._log(f"ERROR: {err:.2e}", "warn" if err > 0 else "dim")

    # ---- helpers ------------------------------------------------------
    def _set_status(self, text, color=T.ACCENT):
        self._status_color = color
        self.status.configure(text=text, fg=color)
        self.cursor.configure(fg=color)

    def _set_progress(self, pct):
        n = int(BAR_W * pct / 100)
        self.bar.configure(text=f"[{'#' * n}{'.' * (BAR_W - n)}] {pct:3d}%")

    def _set_editing(self, enabled):
        self.textbox.configure(state="normal" if enabled else "disabled")
        self.paste_btn.set_enabled(enabled)
        self.clear_btn.set_enabled(enabled)

    def _paste(self):
        try:
            txt = self.clipboard_get()
        except Exception:
            return
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", txt)

    def _clear(self):
        self.textbox.delete("1.0", "end")

    def _settings(self) -> Settings:
        kw = dict(wpm=float(self.wpm_var.get()),
                  typo_rate=float(self.typo_var.get()) / 100.0,
                  revise_mode=MODES.get(self.mode_btn.get(), "random"))
        try:
            return Settings(locate_mode=self.locate_btn.get(), **kw)
        except TypeError:       # engine without locate_mode yet
            return Settings(**kw)

    def _text(self) -> str:
        return self.textbox.get("1.0", "end-1c")

    # ---- start / stop -------------------------------------------------
    def _start_button(self):
        if self.st != "idle":
            return
        if not self._text().strip():
            self._set_status("NO INPUT — paste some text first", T.WARN)
            return
        self._enter_active("countdown")
        self._countdown(3)

    def _countdown(self, n):
        if self.st != "countdown":
            return
        if n <= 0:
            self._begin_typing()
            return
        self._countdown_n = n
        self._ov_dirty = True
        self._set_status(f"ARMED — T-{n}... click into the target field", T.WARN)
        self._after_id = self.after(1000, lambda: self._countdown(n - 1))

    def _hotkey_start(self, delay=250):
        if not self._text().strip():
            self._set_status("NO INPUT — paste some text first", T.WARN)
            if self.overlay:
                self.overlay.flash("NO TEXT")
                self._ov_dirty = True
            return
        self._enter_active("starting")
        self._set_status("ARMED — releasing F8...", T.WARN)
        self._after_id = self.after(delay, self._begin_typing)   # let F8 be released

    def _enter_active(self, state):
        self.st = state
        self._set_editing(False)
        self.start_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)
        self._set_progress(0)
        self._pct = 0
        self._face_p = 0.0
        self._stream = ""
        self._nchars = self._ntypos = self._last_epoch = 0
        self._t0 = time.time()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._ov_result = ""
        self._ov_dirty = True
        self._begin_stats()
        self._log("TRAINING STARTED", "hi")
        self._kick_anim()

    def _plan_revs(self):
        try:
            return len(parse(self._text()).revisions)
        except Exception:
            return None

    def _begin_stats(self):
        try:
            plan = parse(self._text())
            total = len(plan.text) + sum(len(r.revised) for r in plan.revisions)
            nrev = len(plan.revisions)
        except Exception:
            total, nrev = len(self._text()), None
        self.stats.begin(total)
        self.stats.rev_total = nrev

    def _begin_typing(self):
        self._after_id = None
        if self.st not in ("starting", "countdown"):
            return
        text = self._text()
        if self.backend is None:
            try:
                self.backend = PynputBackend()
            except Exception as e:
                self._finish_ui(f"ERROR — cannot send keys: {e}", T.ERR)
                return
        self.st = "typing"
        self._kind = "typing"
        self._t0 = time.time()
        self.stats.begin(self.stats.total)      # restart the clock when keys actually start flowing
        self.stats.rev_total = self._plan_revs()
        self.pause_btn.set_enabled(True)
        self.pause_btn.configure(text="[ F9 PAUSE ]")
        self._set_status("TYPING 0%", T.ACCENT)
        self._log(f"SETTINGS // WPM: {self.wpm_var.get():.0f} TYPO: {self.typo_var.get():.1f}% "
                  f"LOCATE: {self.locate_btn.get().upper()}", "dim")
        base = dict(on_progress=lambda f: self.events.put(("progress", f)),
                    on_status=lambda k: self.events.put(("status", k)),
                    on_finish=lambda r: self.events.put(("finished", r)))
        extra = dict(on_log=lambda s: self.events.put(("log", s)),
                     on_char=lambda c: self.events.put(("char", c)))
        settings = self._settings()
        try:
            self.engine = TypingEngine(self.backend, settings, **base, **extra)
        except TypeError:       # engine without on_log / on_char yet
            self.engine = TypingEngine(self.backend, settings, **base)
        self.engine.start(text)

    def _toggle_pause(self):
        if self.st == "typing" and self.engine:
            self.engine.toggle_pause()

    def _stop(self):
        if self.st in ("starting", "countdown"):
            if self._after_id:
                self.after_cancel(self._after_id)
                self._after_id = None
            self._finish_ui("STOPPED", T.ERR)
        elif self.st == "typing" and self.engine:
            self.engine.stop()

    def _finish_ui(self, message, color=T.ACCENT):
        self.st = "idle"
        self.engine = None
        self.stats.end()
        self._ov_result = "DONE" if message == "DONE" else "STOPPED"
        self._ov_dirty = True
        self._set_editing(True)
        self.start_btn.set_enabled(True)
        self.pause_btn.set_enabled(False)
        self.pause_btn.configure(text="[ F9 PAUSE ]")
        self.stop_btn.set_enabled(False)
        self._set_status(message, color)
        self._draw_face()
        self.after(6000, self._reset_face)

    def _reset_face(self):
        if self.st == "idle" and self._face_p > 0:
            self._face_p = 0.0
            self._draw_face()
            self._set_status(READY)
            self._set_progress(0)
            self._ov_result, self._pct, self._ov_dirty = "", 0, True

    # ---- event pump (Tk thread) --------------------------------------
    def _poll(self):
        try:
            for _ in range(400):
                kind, val = self.events.get_nowait()
                self._handle(kind, val)
        except queue.Empty:
            pass
        self._refresh_stream()
        self._sync_overlay()
        self.after(40 if self.st != "idle" else 100, self._poll)

    def _handle(self, kind, val):
        if kind != "char":
            self._ov_dirty = True
        if kind == "hotkey":
            if val == "f8":
                if self.st == "idle":
                    self._hotkey_start()
                else:
                    self._stop()
            elif val == "f9":
                self._toggle_pause()
        elif kind == "log":
            self._log(str(val))
            self.stats.on_log(val)
        elif kind == "char":
            self._push_char(val)
            self.stats.on_char(val)
        elif kind == "progress":
            old = self._pct
            self._pct = int(val * 100)
            self._face_p = float(val)
            self._set_progress(self._pct)
            self.stats.on_progress(val)
            if self._pct // 10 > old // 10 and 0 < self._pct < 100:
                self._epoch()
            if self.st == "typing" and not (self.engine and self.engine.paused):
                self._set_status(self._typing_text(), T.LAVENDER if self._kind == "revising" else T.ACCENT)
        elif kind == "status" and self.st == "typing":
            if val == "paused":
                self.stats.pause()
                self.pause_btn.configure(text="[ F9 RESUME ]")
                self._set_status("PAUSED — F9 to resume", T.WARN)
                self._log("PAUSED", "warn")
            else:
                self.stats.resume()
                self.stats.on_status(val)
                self._kind = val
                self.pause_btn.configure(text="[ F9 PAUSE ]")
                self._set_status(self._typing_text(), T.LAVENDER if val == "revising" else T.ACCENT)
                self._kick_anim()
        elif kind == "finished":
            msg, col = {"done": ("DONE", T.ACCENT), "stopped": ("STOPPED", T.ERR)}.get(
                val, ("ERROR while typing", T.ERR))
            if val == "done":
                self._set_progress(100)
                self._face_p = 1.0
                self._log("TRAINING COMPLETE", "ok")
            else:
                self._log(msg, "err")
            self._finish_ui(msg, col)

    def _typing_text(self):
        return f"REVISING {self._pct}%" if self._kind == "revising" else f"TYPING {self._pct}%"

    # ---- shutdown -----------------------------------------------------
    def _save_cfg(self):
        try:
            data = {
                "wpm": int(round(self.wpm_var.get())),
                "typo_rate": round(float(self.typo_var.get()), 1),
                "revise_mode": MODES.get(self.mode_btn.get(), "random"),
                "locate_mode": self.locate_btn.get(),
                "always_on_top": self.top_btn.get() == "yes",
                "overlay": self.overlay_btn.get() == "yes",
                "last_text": self._text(),
            }
            if self.overlay is not None:
                x, y = self.overlay.position()
                data.update(overlay_x=x, overlay_y=y)
            self.cfg.update(data)
            config.save(self.cfg)
        except Exception:
            pass

    def _on_close(self):
        self._save_cfg()
        if self.engine:
            self.engine.stop()
        self.hotkeys.stop()
        self.destroy()


def main():
    HumanTyperApp().mainloop()
