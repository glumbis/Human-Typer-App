"""REVIEW window: the AI's proposal for the text the user wrote in another app, with the changes
highlighted. Nothing touches the user's field until APPLY IN FIELD (then the app arms an edit run)."""
import os
import tkinter as tk
from typing import Callable, Optional

import customtkinter as ctk

from .. import plat
from .. import review
from ..ai import prompts
from . import macutil
from . import theme as T
from .widgets import Panel, TextButton, Toggle
from .winutil import fit_to_workarea

OLD_BG = "#3a1030"      # original text that will be replaced
NEW_BG = "#1f2a5c"      # proposed text that will be typed


def master_topmost(master) -> bool:
    """The main window's -topmost WITHOUT master.attributes("-topmost"): on Windows (Tk 8.6) that
    query re-applies HWND_TOPMOST and lifts the main window over us. The all-attributes form is
    read-only on every platform."""
    try:
        a = master.tk.splitlist(master.tk.call("wm", "attributes", str(master)))
        return bool(int(dict(zip(a[::2], a[1::2])).get("-topmost", 0)))
    except Exception:
        return False


class ReviewWindow(ctk.CTkToplevel):
    """on_apply(improved_text) -> error message or None (None: the app armed the edit and closes
    us); on_redo(style) starts a new AI pass (the app calls set_busy / set_result); on_close()."""

    def __init__(self, master, original: str, improved: str, style: str,
                 on_apply: Callable[[str], Optional[str]], on_redo: Callable[[str], None],
                 on_close: Optional[Callable[[], None]] = None, message: str = "", hotkey: str = "F8"):
        super().__init__(master, fg_color=T.BG)
        if T._family is None:
            T.init_fonts(self)
        self.original = original
        self.on_apply, self.on_redo, self.on_close = on_apply, on_redo, on_close
        self.hotkey = hotkey
        self.busy = False
        self._closed = False
        self._hl_id = None
        self._topmost = None
        self.title("Human Typer - REVIEW")
        self.configure(bg=T.BG)
        self.geometry("720x640")
        self.minsize(560, 460)
        try:
            fit_to_workarea(self, 720, 640, 560, 460, ctk.ScalingTracker.get_window_scaling(self), 0.7)
        except Exception:
            pass
        self._set_icon()
        self._build(style)
        self.set_result(improved, message)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._raise_above_master()
        # customtkinter redraws a new toplevel on Windows right after creation: raise again after it
        self.after(80, lambda: self._raise_above_master(focus=False))
        self.after(450, lambda: self._raise_above_master(focus=False))
        self.after(200, self._poll_topmost)

    # ---- z-order (mirrors the main window's ALWAYS ON TOP, like the AI window) -------------------
    def _raise_above_master(self, focus=True):
        try:
            if not self.winfo_exists():
                return
            self._topmost = master_topmost(self.master)
            self.attributes("-topmost", self._topmost)
            self.lift()
            if focus:
                self.focus_force()
        except tk.TclError:
            pass

    def _poll_topmost(self):
        if self._closed:
            return
        top = master_topmost(self.master)
        if top != self._topmost:
            self._topmost = top
            try:
                self.attributes("-topmost", top)
            except tk.TclError:
                return
        self.after(250, self._poll_topmost)

    def _set_icon(self):
        if plat.IS_MAC:
            macutil.set_icon(self, T.resource_path(os.path.join("assets", "icon.png")), False)
            return
        path = T.resource_path(os.path.join("assets", "icon.ico"))
        if os.path.exists(path):
            try:
                self.after(300, lambda: self.winfo_exists() and self.iconbitmap(path))
            except Exception:
                pass

    # ---- UI ----------------------------------------------------------------------------------
    def _text_font(self):
        return ctk.CTkFont(family=T.font().actual("family"), size=T.scale_size(12))

    def _build(self, style):
        f9, f10 = T.font(9), T.font(10)
        tk.Label(self, text="// review :: the AI's suggestion for the text in your field :: nothing changes "
                            "there until APPLY //", font=f9, fg=T.DIM, bg=T.BG, anchor="w", justify="left",
                 wraplength=690).pack(fill="x", padx=14, pady=(10, 4))

        op = Panel(self, "YOUR TEXT // red = will be replaced", f9)
        op.pack(fill="both", expand=True, padx=10, pady=(2, 0))
        self.old = tk.Text(op.body, wrap="word", height=6, font=self._text_font(), bg=T.INPUT_BG, fg=T.DIM,
                           relief="flat", highlightthickness=1, highlightbackground=T.LINE, bd=0,
                           selectbackground=T.VIOLET, selectforeground=T.WHITE, padx=6, pady=4)
        self.old.pack(fill="both", expand=True)
        self.old.tag_configure("old", background=OLD_BG, foreground=T.ERR, overstrike=True)

        np_ = Panel(self, "IMPROVED // editable, blue = will be typed", f9)
        np_.pack(fill="both", expand=True, padx=10, pady=(6, 0))
        self.new = ctk.CTkTextbox(np_.body, wrap="word", height=140, font=self._text_font(), fg_color=T.INPUT_BG,
                                  text_color=T.TEXT, border_width=1, border_color=T.LINE, corner_radius=0,
                                  scrollbar_button_color=T.LINE, scrollbar_button_hover_color=T.VIOLET)
        self.new.pack(fill="both", expand=True)
        tb = self.new._textbox
        tb.configure(insertbackground=T.CYAN, insertwidth=2, selectbackground=T.VIOLET, selectforeground=T.WHITE)
        tb.tag_configure("new", background=NEW_BG, foreground=T.ICE)
        tb.bind("<KeyRelease>", lambda e: self._schedule_highlight(), add="+")
        tb.bind("<<Paste>>", lambda e: self._schedule_highlight(), add="+")

        row = tk.Frame(self, bg=T.BG)
        row.pack(fill="x", padx=14, pady=(8, 0))
        tk.Label(row, text="STYLE", font=f10, fg=T.DIM, bg=T.BG, width=7, anchor="w").pack(side="left")
        self.style_btn = Toggle(row, list(prompts.IMPROVE_STYLES),
                                style if style in prompts.IMPROVE_STYLES else "clearer",
                                command=lambda v: self._info(f"{v}: {prompts.IMPROVE_HINTS.get(v, '')} "
                                                             "- press REDO"))
        self.style_btn.pack(side="left")
        self.redo_btn = TextButton(row, "[ REDO ]", self._redo, fnt=T.font(10, True), color=T.LAVENDER)
        self.redo_btn.pack(side="left", padx=(8, 0))

        self.msg_lbl = tk.Label(self, text="", font=f10, fg=T.DIM, bg=T.BG, anchor="w", justify="left",
                                wraplength=690)
        self.msg_lbl.pack(fill="x", padx=14, pady=(4, 0))

        bot = tk.Frame(self, bg=T.BG)
        bot.pack(fill="x", padx=14, pady=(8, 12))
        self.apply_btn = TextButton(bot, "[ APPLY IN FIELD ]", self._apply, fnt=T.font(11, True))
        self.apply_btn.pack(side="left")
        self.copy_btn = TextButton(bot, "[ COPY ]", self._copy, fnt=T.font(11, True), color=T.LAVENDER)
        self.copy_btn.pack(side="left", padx=(12, 0))
        self.cancel_btn = TextButton(bot, "[ CANCEL ]", self.cancel, fnt=T.font(11, True), color=T.PINK)
        self.cancel_btn.pack(side="left", padx=(12, 0))
        self.count_lbl = tk.Label(bot, text="", font=f9, fg=T.DIM, bg=T.BG)
        self.count_lbl.pack(side="right")

    # ---- content -----------------------------------------------------------------------------
    def improved(self) -> str:
        return self.new.get("1.0", "end-1c")

    def style(self) -> str:
        return self.style_btn.get() or "clearer"

    def _info(self, text, color=None):
        try:
            self.msg_lbl.configure(text=text, fg=color or T.DIM)
        except tk.TclError:
            pass

    def set_busy(self, busy: bool, message: str = ""):
        self.busy = busy
        for b in (self.apply_btn, self.redo_btn):
            b.set_enabled(not busy)
        self.new.configure(state="disabled" if busy else "normal")
        if message:
            self._info(message, T.DIM)

    def set_result(self, improved: str, message: str = ""):
        """Show a (new) proposal."""
        self.set_busy(False)
        self.old.configure(state="normal")
        self.old.delete("1.0", "end")
        self.old.insert("1.0", self.original)
        self.old.configure(state="disabled")
        self.new.delete("1.0", "end")
        self.new.insert("1.0", improved)
        self._highlight()
        if message:
            self._info(message, T.ACCENT)

    def _schedule_highlight(self):
        if self._hl_id is not None:
            try:
                self.after_cancel(self._hl_id)
            except Exception:
                pass
        self._hl_id = self.after(250, self._highlight)

    def _highlight(self):
        self._hl_id = None
        if self._closed:
            return
        new = self.improved()
        edits = review.compute_edits(self.original, new)
        old_tb, new_tb = self.old, self.new._textbox
        old_tb.tag_remove("old", "1.0", "end")
        new_tb.tag_remove("new", "1.0", "end")
        for s, e, _rep in edits:
            if e > s:
                old_tb.tag_add("old", f"1.0 + {s} chars", f"1.0 + {e} chars")
        for a, b in review.improved_spans(edits):
            if b > a:
                new_tb.tag_add("new", f"1.0 + {a} chars", f"1.0 + {b} chars")
        n = len(edits)
        self.count_lbl.configure(text=f"{n} edit{'s' if n != 1 else ''}" if n else "no changes")
        self.n_edits = n

    # ---- actions -----------------------------------------------------------------------------
    def _redo(self):
        if self.busy:
            return
        self.set_busy(True, f"improving again ({self.style()})...")
        self.on_redo(self.style())

    def _apply(self):
        if self.busy:
            return
        err = self.on_apply(self.improved())
        if err:
            self._info(err, T.PINK)

    def _copy(self):
        t = self.improved()
        if t:
            self.clipboard_clear()
            self.clipboard_append(t)
            self._info("copied the improved text to the clipboard.", T.ACCENT)

    def cancel(self):
        self.close()
        if self.on_close:
            try:
                self.on_close()
            except Exception:
                pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.destroy()
        except tk.TclError:
            pass
