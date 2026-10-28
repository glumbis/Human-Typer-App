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
from .ui.background import PROBE_CHARS, GalaxyBackground
from .ui.widgets import (AsciiView, CanvasArt, GBox, GButton, GLabel, GToggle, GWindow, Panel,
                         TextButton)
from .ui.overlay import StatsOverlay
from .ui.stats import Stats
from .ui.winutil import fit_to_workarea

READY = "READY — click into any app and press F8"
MODES = {"RANDOM": "random", "END": "end"}
LOCATE = ("auto", "clipboard", "count")
BAR_W = 30
STREAM_W = 26
LOG_N = 6


class HumanTyperApp(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        super().__init__()
        T.init_fonts(self)
        self.title("HUMAN TYPER")
        self.geometry("800x700")
        self.minsize(760, 660)
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
        self._status_text = ""
        self._minimized = False

        self._build()
        self._fit_to_screen()
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
        self.bind("<Unmap>", self._on_unmap, add="+")
        self.bind("<Map>", self._on_map, add="+")
        self.after(250, self._kick_anim)

    def _fit_to_screen(self):
        try:
            fit_to_workarea(self, 800, 700, 760, 560, ctk.ScalingTracker.get_window_scaling(self))
        except Exception:
            pass

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
        glyphs = T.unmeasured_ok(self, T.font(8), list(".·˙'`,+*✦⋆") + list(PROBE_CHARS))
        self.bg = GalaxyBackground(self, T.font(8), glyphs)
        cv = self.bg.canvas
        self._cv = cv

        # banner: painted straight onto the galaxy ----------------------
        self.banner = art.Banner("HUMAN TYPER", glyph_ok=glyphs, density=0.07)
        self.banner_view = CanvasArt(cv, T.font(7), 14, 12, art.Banner.tag_colors(), "bn", self.banner.rows)
        sub_y = 12 + self.banner_view.height() + 2
        self.sub_lbl = GLabel(cv, f"// human-like keystroke engine  ::  v{__version__}  ::  "
                                  "F8 start/stop  F9 pause //", f9, T.DIM)
        self.sub_lbl.moveto(14, sub_y)
        self._sub_y = sub_y
        self.rowconfigure(0, minsize=int(sub_y + f9.metrics("linespace") + 8))
        self.ai_top_btn = GButton(cv, "[ ✦ AI WRITE ]", self._open_ai, fnt=T.font(10, True), color=T.PINK)

        # left column: input (opaque) ------------------------------------
        inp = self.inp_panel = Panel(self, "PASTE TEXT // [first][revised] = later revision", f9, bg=T.PANEL)
        inp.grid(row=1, column=0, sticky="nsew", padx=(10, 4))
        self.textbox = ctk.CTkTextbox(
            inp.body, wrap="word", font=ctk.CTkFont(family=T.font().actual("family"), size=13),
            fg_color=T.INPUT_BG, text_color=T.TEXT, border_width=1, border_color=T.LINE,
            corner_radius=0, scrollbar_button_color=T.LINE, scrollbar_button_hover_color=T.VIOLET,
            height=90)
        self.textbox.pack(fill="both", expand=True)
        self.textbox._textbox.configure(insertbackground=T.CYAN, insertwidth=2,
                                        selectbackground=T.VIOLET, selectforeground=T.WHITE)
        if self.cfg.get("last_text"):
            self.textbox.insert("1.0", self.cfg["last_text"])
        # AI WRITE right on the paste box (title line) and a hint inside it while it is empty
        ai_tb = TextButton(inp, "[ ✦ AI WRITE ]", self._open_ai, fnt=T.font(10, True), color=T.PINK)
        ai_tb.default_bg = T.PANEL
        ai_tb.configure(bg=T.PANEL)
        ai_tb.place(relx=1.0, x=-14, y=-2, anchor="ne")
        hint = self._hint = tk.Frame(self.textbox, bg=T.INPUT_BG)
        for txt, col in (("paste or type the text to be typed here", T.DIM), ("— or —", T.LINE)):
            lb = tk.Label(hint, text=txt, font=T.font(10), fg=col, bg=T.INPUT_BG)
            lb.pack(pady=3)
            lb.bind("<Button-1>", lambda e: self.textbox.focus_set())
        hb = TextButton(hint, "[ ✦ AI WRITE ]  let a local AI write it", self._open_ai,
                        fnt=T.font(11, True), color=T.PINK)
        hb.default_bg = T.INPUT_BG
        hb.configure(bg=T.INPUT_BG)
        hb.pack(pady=3)
        hint.bind("<Button-1>", lambda e: self.textbox.focus_set())
        self._hint_shown = False

        # command menu: ghost widgets on the galaxy ------------------------
        self._row_h = 24
        self._menu_top = f9.metrics("linespace") + 12
        self.rowconfigure(2, minsize=8 + self._menu_top + 9 * self._row_h + 10)
        self.menu_box = GBox(cv, "COMMAND MENU", f9)
        self._menu_items = []          # (ghost, col_x, row)
        lab_w = f10.measure("M") * 22

        def add(g, col, row):
            self._menu_items.append((g, col, row))
            return g

        add(GLabel(cv, "1)", f10, T.DIM), 0, 0)
        self.paste_btn = add(GButton(cv, "[ PASTE ]", self._paste), f10.measure("M") * 3, 0)
        add(GLabel(cv, "2)", f10, T.DIM), f10.measure("M") * 15, 0)
        self.clear_btn = add(GButton(cv, "[ CLEAR ]", self._clear), f10.measure("M") * 18, 0)
        add(GButton(cv, "[ ✦ AI WRITE ]", self._open_ai, color=T.PINK), f10.measure("M") * 30, 0)

        self.wpm_var = ctk.DoubleVar(value=float(self.cfg["wpm"]))
        self.typo_var = ctk.DoubleVar(value=float(self.cfg["typo_rate"]))
        self.wpm_lbl = self._slider(add, 1, "3) WPM", self.wpm_var, 10, 200, 190, self._wpm_text, lab_w)
        self.typo_lbl = self._slider(add, 2, "4) TYPO RATE", self.typo_var, 0, 10, 100, self._typo_text, lab_w)

        cur = next((k for k, v in MODES.items() if v == self.cfg["revise_mode"]), "RANDOM")
        self.mode_btn = GToggle(cv, list(MODES), cur)
        self._menu_row(add, 3, "5) REVISE", self.mode_btn, lab_w)

        loc = self.cfg.get("locate_mode", "auto")
        self.locate_btn = GToggle(cv, LOCATE, loc if loc in LOCATE else "auto")
        self._menu_row(add, 4, "6) LOCATE", self.locate_btn, lab_w)

        self.top_btn = GToggle(cv, ["yes", "no"], "yes" if self.cfg["always_on_top"] else "no",
                               command=lambda v: self.attributes("-topmost", v == "yes"))
        self._menu_row(add, 5, "7) ALWAYS ON TOP", self.top_btn, lab_w)

        self.overlay_btn = GToggle(cv, ["yes", "no"], "yes" if self.cfg.get("overlay", True) else "no",
                                   command=lambda v: self._set_overlay(v == "yes"))
        self._menu_row(add, 6, "8) OVERLAY", self.overlay_btn, lab_w)

        self.ai_btn = GButton(cv, "[ OPEN ]", self._open_ai, color=T.PINK)
        self._menu_row(add, 7, "9) AI WRITE", self.ai_btn, lab_w)

        self.anim_btn = GToggle(cv, ["on", "off"], "on" if self.cfg.get("animation", True) else "off",
                                command=lambda v: self._set_animation(v == "on"))
        self._menu_row(add, 8, "10) ANIMATION", self.anim_btn, lab_w)

        # right column: face + log drawn straight on the galaxy ------------
        f6, f8 = T.font(6), T.font(8)
        self._face_w = art.FW * f6.measure("M")
        self.columnconfigure(1, minsize=self._face_w + 44)
        self.face_box = GBox(cv, "SUBJECT_00", f9)
        self.face_view = CanvasArt(cv, f6, 0, 0, art.Face.tag_colors(), "face", art.FH, squeeze=0)
        self.face_cap = GLabel(cv, "", f8, T.DIM)
        self.log_box = GBox(cv, "LOG", f9)
        self._log_colors = {"dim": T.DIM, "ok": T.CYAN, "warn": T.PINK, "err": T.ERR,
                            "rev": T.LAVENDER, "hi": T.ICE, None: T.TEXT}
        self._log_lines = []                       # (stamp, text, tag)
        self._log_items = [(GLabel(cv, "", f8, T.DIM), GLabel(cv, "", f8, T.TEXT)) for _ in range(LOG_N)]
        self.stream_lbl = GLabel(cv, "", T.font(8, True), T.ACCENT)
        self._base_trects, self._opaque = [], []

        # bottom: status + buttons, ghost too -----------------------------
        f11 = T.font(11, True)
        self.rowconfigure(3, minsize=f9.metrics("linespace") + 10 + f11.metrics("linespace") * 2 + 34)
        self.status_box = GBox(cv, "STATUS", f9)
        self.status_pre = GLabel(cv, "STATUS >", f11, T.DIM)
        self.status = GLabel(cv, "", f11, T.ACCENT)
        self.bar = GLabel(cv, "", f10, T.ACCENT)
        self.start_btn = GButton(cv, "[ F8 START ]", self._start_button, fnt=f11)
        self.pause_btn = GButton(cv, "[ F9 PAUSE ]", self._toggle_pause, fnt=f11, color=T.WARN)
        self.stop_btn = GButton(cv, "[ STOP ]", self._stop, fnt=f11, color=T.ERR)
        self.pause_btn.set_enabled(False)
        self.stop_btn.set_enabled(False)
        self._set_progress(0)
        self._wpm_text(self.wpm_var.get())
        self._typo_text(self.typo_var.get())
        self._lay_id = None
        cv.bind("<Configure>", self._schedule_layout, add="+")

    def _menu_row(self, add, r, name, widget, lab_w):
        add(GLabel(self._cv, self._dots(name), T.font(10), T.TEXT), 0, r)
        add(widget, lab_w, r)

    @staticmethod
    def _dots(name, width=20):
        return (name + " ").ljust(width, ".") + " "

    def _slider(self, add, r, name, var, lo, hi, steps, fmt, lab_w):
        add(GLabel(self._cv, self._dots(name), T.font(10), T.TEXT), 0, r)
        s = ctk.CTkSlider(self._cv, from_=lo, to=hi, number_of_steps=steps, variable=var, command=fmt,
                          height=14, width=170, corner_radius=0, button_corner_radius=0, button_length=6,
                          fg_color=T.BG3, progress_color=T.VIOLET, button_color=T.CYAN,
                          button_hover_color=T.PINK, bg_color=T.BG, border_width=0)
        win = self._cv.create_window(0, 0, window=s, anchor="nw", width=170, height=16)
        add(GWindow(self._cv, win), lab_w, r)
        lbl = GLabel(self._cv, "", T.font(10), T.ACCENT)
        add(lbl, lab_w + 182, r)
        return lbl

    # ---- ghost layout --------------------------------------------------
    def _schedule_layout(self, _e=None):
        if self._lay_id is None:
            self._lay_id = self.after(30, self._layout)

    def _layout(self):
        self._lay_id = None
        try:
            mx, my, mw, mh = self.grid_bbox(0, 2)
            sx, sy, sw, sh = self.grid_bbox(0, 3, 1, 3)
        except Exception:
            return
        if mw < 50 or sw < 50:
            return
        f9 = T.font(9)
        cv = self._cv
        x0, y0 = mx + 10, my + 8
        for g, cx, r in self._menu_items:
            g.moveto(x0 + 16 + cx, y0 + self._menu_top - 4 + r * self._row_h + (3 if isinstance(g, GWindow) else 0))
        trects = []
        for g, _cx, _r in self._menu_items:
            b = cv.bbox(g.item if isinstance(g, GWindow) else g.tag)
            if b:
                trects.append(b)
        x1 = max([b[2] for b in trects] + [x0 + 200]) + 22
        y1 = my + mh - 8
        self.menu_box.draw(x0, y0, x1, y1)
        trects.append((x0, y0, x1, y0 + f9.metrics("linespace") + 4))          # box title
        # status box
        bx0, by0, bx1, by1 = sx + 10, sy + 2, sx + sw - 10, sy + sh - 10
        self.status_box.draw(bx0, by0, bx1, by1)
        ly = by0 + f9.metrics("linespace") + 8
        self.status_pre.moveto(bx0 + 16, ly)
        self.status.moveto(bx0 + 16 + self.status_pre.width() + 8, ly)
        lh = T.font(11, True).metrics("linespace")
        by = ly + lh + 8
        self.bar.moveto(bx0 + 16, by + 2)
        x = bx1 - 14
        for b in (self.stop_btn, self.pause_btn, self.start_btn):
            x -= b.width() + 4
            b.moveto(x, by)
            x -= 10
        trects.append((bx0, by0, bx0 + 150, by0 + f9.metrics("linespace") + 4))
        trects.append((bx0 + 10, ly - 2, bx1 - 10, ly + lh + 2))
        bb = cv.bbox(self.bar.tag)
        if bb:
            trects.append(bb)
        bb = cv.bbox(self.start_btn.tag)
        if bb:
            trects.append((x + 10, bb[1], bx1, bb[3]))
        bw = self.banner_view
        trects.append((bw.x + self.banner.PAD_X * bw.cw, bw.y, bw.x + (self.banner.PAD_X + self.banner.fw) * bw.cw,
                       bw.y + bw.height()))
        sb = cv.bbox(self.sub_lbl.tag)
        if sb:
            trects.append(sb)
        wdg = self.inp_panel
        opaque = [(wdg.winfo_x(), wdg.winfo_y(), wdg.winfo_x() + wdg.winfo_width(),
                   wdg.winfo_y() + wdg.winfo_height())]
        W = self.winfo_width()
        ab = self.ai_top_btn
        ab.moveto(W - ab.width() - 14, self._sub_y - 4)
        b = ab.bbox()
        if b:
            trects.append(b)
        # right column: face box on top, log box below, both painted on the galaxy
        rx0, rx1 = opaque[0][2] + 8, W - 10
        ry0 = opaque[0][1] - 2
        f6, f8 = T.font(6), T.font(8)
        fx = rx0 + max(8, (rx1 - rx0 - self._face_w) // 2)
        fy = ry0 + f9.metrics("linespace") + 6
        fv = self.face_view
        if (fv.x, fv.y) != (fx, fy):
            fv.x, fv.y = fx, fy
            fv._last = [None] * fv.rows
            cv.delete("face")
        face_h = fv.height()
        self.face_cap.moveto(fx, fy + face_h + 4)
        fy1 = fy + face_h + 6 + f8.metrics("linespace") + 6
        self.face_box.draw(rx0, ry0, rx1, fy1)
        trects.append((fx, fy, fx + self._face_w, fy + face_h))
        trects.append((rx0, ry0, rx0 + 150, ry0 + f9.metrics("linespace") + 4))
        cb = self.face_cap.bbox()
        trects.append((fx, fy + face_h + 2, fx + self._face_w, (cb[3] if cb else fy1)))
        ly0, ly1 = fy1 + 10, by0 - 6
        self.log_box.draw(rx0, ly0, rx1, ly1)
        trects.append((rx0, ly0, rx0 + 90, ly0 + f9.metrics("linespace") + 4))
        lh = f8.metrics("linespace") + 2
        tx = rx0 + 12
        self._log_geom = (tx, ly0 + f9.metrics("linespace") + 6, lh, rx1 - 12)
        self.stream_lbl.moveto(tx, ly1 - lh - 4)
        trects.append((tx, ly1 - lh - 6, rx1 - 10, ly1 - 2))
        self._log_cols = max(10, (rx1 - tx - 12) // max(1, f8.measure("M")))
        self._base_trects, self._opaque = trects, opaque
        log_free = ly0 + f9.metrics("linespace") + 6 + LOG_N * lh
        self.bg.set_focus(((rx0 + rx1) / 2.0, (log_free + ly1 - lh) / 2.0 + 6),
                          ((bw.x + (self.banner.PAD_X + self.banner.fw) * bw.cw + W) / 2.0 + 10, bw.y + bw.height() / 2.0))
        self._render_log()
        self.bg.set_zones([(0, 0, W, bw.y + bw.height()), (x1, y0, rx0, y1), (rx0, log_free, W, ly1),
                           (0, y1, W, by0 + 20)])

    def _wpm_text(self, v):
        v = int(round(float(v)))
        self.wpm_lbl.configure(text=f"{T.ascii_bar(v, 10, 200, 10)} {v}")

    def _typo_text(self, v):
        v = float(v)
        val = "OFF" if v < 0.05 else f"{v:.1f}%"
        self.typo_lbl.configure(text=f"{T.ascii_bar(v, 0, 10, 10)} {val}")

    # ---- AI writer hook ------------------------------------------------
    def _open_ai(self):
        try:
            from .ui.ai_window import open_ai_window
        except Exception as e:
            self._log(f"AI module unavailable: {e}", "err")
            return
        try:
            open_ai_window(self, on_use_text=self._use_ai_text)
        except Exception as e:
            self._log(f"AI window failed: {e}", "err")

    def _use_ai_text(self, text):
        if self.st != "idle":
            self._log("Cannot replace text while typing", "warn")
            return
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", text)

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
        ph = self._phase if self.st != "idle" else self._phase // 2     # slower nebula turn at idle
        self.face_view.paint(self._face.cells(self._face_p, ph, glitch=self.st != "idle"))
        pct = int(self._face_p * 100)
        self.face_cap.configure(text=f"COHERENCE {100 - pct:3d}%  STARDUST {pct:3d}%")

    def _animating(self):
        return self.st != "idle" and not (self.engine and self.engine.paused)

    def _visible(self):
        try:
            return self.state() != "iconic" and bool(self.winfo_viewable())
        except tk.TclError:
            return False

    def _kick_anim(self):
        """Start (or keep) the always-on animation loop: backdrop, banner and face."""
        if self._anim_id is None:
            self._anim_id = self.after(100, self._anim)

    def _on_unmap(self, e):
        if e.widget is self:
            self._minimized = True

    def _on_map(self, e):
        if e.widget is self:
            self._minimized = False

    def _set_animation(self, on):
        """ANIMATION off freezes the galaxy, banner and face on their current frame (no CPU)."""
        self.anim_btn.set("on" if on else "off")
        self._save_cfg()

    def _anim(self):
        self._anim_id = None
        typing = self._animating()
        delay = 400
        if self.anim_btn.get() == "off" and self._phase > 0:
            pass                                   # frozen; keep polling cheaply for the toggle
        elif self._visible() and not self._minimized:
            self._phase += 1
            self._hue = (self._hue + (0.01 if typing else 0.004)) % 2.0
            self.bg.step(typing)
            if self._phase % 2 == 0:
                self._draw_banner()
            if self._phase % (2 if typing else 4) == 0:
                self._draw_face()
            delay = 120 if typing else 160
        self._anim_id = self.after(delay, self._anim)

    def _blink(self):
        self._cursor_on = not self._cursor_on
        self._render_status()
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
        self._log_lines = (self._log_lines + [(stamp, text, tag)])[-LOG_N:]
        self._render_log()

    def _render_log(self):
        """Paint the last LOG_N lines on the galaxy and keep the backdrop out from under them."""
        geom = getattr(self, "_log_geom", None)
        if not geom:
            return
        tx, ty, lh, xmax = geom
        sw = T.font(8).measure("00:00 ")
        rects = []
        lines = self._log_lines[-LOG_N:]
        for i, (st_lbl, tx_lbl) in enumerate(self._log_items):
            if i < len(lines):
                stamp, text, tag = lines[i]
                text = text if len(text) <= self._log_cols - 6 else text[:self._log_cols - 7] + "…"
                st_lbl.configure(text=stamp)
                tx_lbl.configure(text=text, fg=self._log_colors.get(tag, T.TEXT))
                st_lbl.moveto(tx, ty + i * lh)
                tx_lbl.moveto(tx + sw, ty + i * lh)
                b = tx_lbl.bbox()
                rects.append((tx, ty + i * lh, (b[2] if b else tx + sw), ty + (i + 1) * lh))
            else:
                st_lbl.configure(text="")
                tx_lbl.configure(text="")
        self.bg.set_mask(self._base_trects + rects, self._opaque)

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
            tail = self._stream[-max(STREAM_W, getattr(self, "_log_cols", STREAM_W) - 12):]
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
        self._status_text = text
        self._render_status()

    def _render_status(self):
        self.status.configure(text=self._status_text + ("█" if self._cursor_on else " "),
                              fg=self._status_color)

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
        self._log_lines = []
        self._render_log()
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
        self._update_hint()
        self.after(40 if self.st != "idle" else 100, self._poll)

    def _update_hint(self):
        """Show the 'paste here / AI WRITE' hint only while the paste box is empty."""
        empty = self.textbox._textbox.compare("end-1c", "==", "1.0")
        if empty != self._hint_shown:
            self._hint_shown = empty
            if empty:
                self._hint.place(relx=0.5, rely=0.45, anchor="center")
            else:
                self._hint.place_forget()

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
            self.bg.keystroke()
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
                "animation": self.anim_btn.get() == "on",
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
        try:
            from .ai import shutdown_ai
            shutdown_ai()
        except Exception:
            pass
        self.destroy()


def main():
    HumanTyperApp().mainloop()
