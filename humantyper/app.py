"""customtkinter GUI for Human Typer."""
import queue

import customtkinter as ctk

from . import config
from .backends import PynputBackend
from .engine import Settings, TypingEngine
from .hotkeys import GlobalHotkeys

READY = "Ready — click into any app and press F8"
MODES = {"Random, while typing": "random", "All at the end": "end"}
HINT = ("Paste your text below. Use [first][revised] to type 'first', then go back later\n"
        "and replace it with 'revised' (e.g. I [realy][really] like it). Use \\[ and \\] for literal brackets.")


class HumanTyperApp(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title("Human Typer")
        self.geometry("560x680")
        self.minsize(480, 600)

        self.cfg = config.load()
        self.events: "queue.Queue" = queue.Queue()
        self.state = "idle"            # idle | starting | countdown | typing
        self.engine = None
        self.backend = None
        self._after_id = None
        self._pct = 0
        self._kind = "typing"

        self._build()
        self.attributes("-topmost", bool(self.cfg["always_on_top"]))

        self.hotkeys = GlobalHotkeys(lambda: self.events.put(("hotkey", "f8")),
                                     lambda: self.events.put(("hotkey", "f9")))
        try:
            self.hotkeys.start()
        except Exception as e:  # pragma: no cover
            self._set_status(f"Hotkeys unavailable: {e}")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(40, self._poll)

    # ---- UI -----------------------------------------------------------
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        pad = {"padx": 16}

        ctk.CTkLabel(self, text="Human Typer", font=ctk.CTkFont(size=24, weight="bold")
                     ).grid(row=0, column=0, sticky="w", pady=(14, 0), **pad)
        ctk.CTkLabel(self, text=HINT, justify="left", anchor="w", text_color=("gray35", "gray65"),
                     wraplength=520).grid(row=1, column=0, sticky="we", pady=(2, 6), **pad)

        self.textbox = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(size=14))
        self.textbox.grid(row=2, column=0, sticky="nsew", **pad)
        if self.cfg.get("last_text"):
            self.textbox.insert("1.0", self.cfg["last_text"])

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=3, column=0, sticky="we", pady=(6, 0), **pad)
        self.paste_btn = ctk.CTkButton(row, text="Paste", width=80, command=self._paste)
        self.paste_btn.pack(side="left")
        self.clear_btn = ctk.CTkButton(row, text="Clear", width=80, command=self._clear,
                                       fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"))
        self.clear_btn.pack(side="left", padx=8)

        opts = ctk.CTkFrame(self)
        opts.grid(row=4, column=0, sticky="we", pady=10, **pad)
        opts.columnconfigure(1, weight=1)

        self.wpm_var = ctk.DoubleVar(value=float(self.cfg["wpm"]))
        self.typo_var = ctk.DoubleVar(value=float(self.cfg["typo_rate"]))
        self.wpm_lbl = self._slider(opts, 0, "Speed", self.wpm_var, 10, 200, 190, self._wpm_text)
        self.typo_lbl = self._slider(opts, 1, "Typo rate", self.typo_var, 0, 10, 20, self._typo_text)

        ctk.CTkLabel(opts, text="Revise when").grid(row=2, column=0, sticky="w", padx=10, pady=6)
        self.mode_btn = ctk.CTkSegmentedButton(opts, values=list(MODES))
        self.mode_btn.grid(row=2, column=1, columnspan=2, sticky="we", padx=10, pady=6)
        cur = next((k for k, v in MODES.items() if v == self.cfg["revise_mode"]), "Random, while typing")
        self.mode_btn.set(cur)

        self.top_var = ctk.BooleanVar(value=bool(self.cfg["always_on_top"]))
        ctk.CTkCheckBox(opts, text="Always on top", variable=self.top_var,
                        command=lambda: self.attributes("-topmost", self.top_var.get())
                        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 10))

        self.status = ctk.CTkLabel(self, text=READY, font=ctk.CTkFont(size=18, weight="bold"),
                                   wraplength=520)
        self.status.grid(row=5, column=0, **pad)
        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)
        self.progress.grid(row=6, column=0, sticky="we", pady=8, **pad)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=7, column=0, sticky="we", **pad)
        for i in range(3):
            btns.columnconfigure(i, weight=1)
        self.start_btn = ctk.CTkButton(btns, text="Start (3 s)", height=42, command=self._start_button)
        self.start_btn.grid(row=0, column=0, sticky="we", padx=3)
        self.pause_btn = ctk.CTkButton(btns, text="Pause", height=42, state="disabled",
                                       command=self._toggle_pause)
        self.pause_btn.grid(row=0, column=1, sticky="we", padx=3)
        self.stop_btn = ctk.CTkButton(btns, text="Stop", height=42, state="disabled",
                                      fg_color="#b03a3a", hover_color="#8e2d2d", command=self._stop)
        self.stop_btn.grid(row=0, column=2, sticky="we", padx=3)

        ctk.CTkLabel(self, text="F8 start / stop    •    F9 pause / resume",
                     text_color=("gray35", "gray65")).grid(row=8, column=0, pady=(8, 12))
        self._wpm_text(self.wpm_var.get())
        self._typo_text(self.typo_var.get())

    def _slider(self, parent, r, name, var, lo, hi, steps, fmt):
        ctk.CTkLabel(parent, text=name).grid(row=r, column=0, sticky="w", padx=10, pady=6)
        s = ctk.CTkSlider(parent, from_=lo, to=hi, number_of_steps=steps, variable=var, command=fmt)
        s.grid(row=r, column=1, sticky="we", padx=10)
        lbl = ctk.CTkLabel(parent, width=80, anchor="e")
        lbl.grid(row=r, column=2, padx=10)
        return lbl

    def _wpm_text(self, v):
        self.wpm_lbl.configure(text=f"{int(round(float(v)))} WPM")

    def _typo_text(self, v):
        v = float(v)
        self.typo_lbl.configure(text="Off" if v < 0.25 else f"{v:.1f} %")

    # ---- helpers ------------------------------------------------------
    def _set_status(self, text):
        self.status.configure(text=text)

    def _set_editing(self, enabled):
        self.textbox.configure(state="normal" if enabled else "disabled")
        st = "normal" if enabled else "disabled"
        self.paste_btn.configure(state=st)
        self.clear_btn.configure(state=st)

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
        return Settings(wpm=float(self.wpm_var.get()),
                        typo_rate=float(self.typo_var.get()) / 100.0,
                        revise_mode=MODES.get(self.mode_btn.get(), "random"))

    def _text(self) -> str:
        return self.textbox.get("1.0", "end-1c")

    # ---- start / stop -------------------------------------------------
    def _start_button(self):
        if self.state != "idle":
            return
        if not self._text().strip():
            self._set_status("Paste some text first")
            return
        self._enter_active("countdown")
        self._countdown(3)

    def _countdown(self, n):
        if self.state != "countdown":
            return
        if n <= 0:
            self._begin_typing()
            return
        self._set_status(f"Starting in {n}… click into the target field")
        self._after_id = self.after(1000, lambda: self._countdown(n - 1))

    def _hotkey_start(self):
        if not self._text().strip():
            self._set_status("Paste some text first")
            return
        self._enter_active("starting")
        self._set_status("Starting…")
        self._after_id = self.after(250, self._begin_typing)   # let F8 be released

    def _enter_active(self, state):
        self.state = state
        self._set_editing(False)
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.set(0)
        self._pct = 0

    def _begin_typing(self):
        self._after_id = None
        if self.state not in ("starting", "countdown"):
            return
        text = self._text()
        if self.backend is None:
            try:
                self.backend = PynputBackend()
            except Exception as e:
                self._finish_ui(f"Cannot send keys: {e}")
                return
        self.state = "typing"
        self._kind = "typing"
        self.pause_btn.configure(state="normal", text="Pause")
        self._set_status("Typing… 0%")
        self.engine = TypingEngine(
            self.backend, self._settings(),
            on_progress=lambda f: self.events.put(("progress", f)),
            on_status=lambda k: self.events.put(("status", k)),
            on_finish=lambda r: self.events.put(("finished", r)))
        self.engine.start(text)

    def _toggle_pause(self):
        if self.state == "typing" and self.engine:
            self.engine.toggle_pause()

    def _stop(self):
        if self.state in ("starting", "countdown"):
            if self._after_id:
                self.after_cancel(self._after_id)
                self._after_id = None
            self._finish_ui("Stopped")
        elif self.state == "typing" and self.engine:
            self.engine.stop()

    def _finish_ui(self, message):
        self.state = "idle"
        self.engine = None
        self._set_editing(True)
        self.start_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="Pause")
        self.stop_btn.configure(state="disabled")
        self._set_status(message)

    # ---- event pump (Tk thread) --------------------------------------
    def _poll(self):
        try:
            while True:
                kind, val = self.events.get_nowait()
                self._handle(kind, val)
        except queue.Empty:
            pass
        self.after(40, self._poll)

    def _handle(self, kind, val):
        if kind == "hotkey":
            if val == "f8":
                if self.state == "idle":
                    self._hotkey_start()
                else:
                    self._stop()
            elif val == "f9":
                self._toggle_pause()
        elif kind == "progress":
            self.progress.set(val)
            self._pct = int(val * 100)
            if self._kind == "typing" and self.state == "typing" and not (self.engine and self.engine.paused):
                self._set_status(f"Typing… {self._pct}%")
        elif kind == "status" and self.state == "typing":
            if val == "paused":
                self.pause_btn.configure(text="Resume")
                self._set_status("Paused (F9 to resume)")
            else:
                self._kind = val
                self.pause_btn.configure(text="Pause")
                if val == "typing":
                    self._set_status(f"Typing… {self._pct}%")
                elif val == "revising":
                    self._set_status("Revising…")
        elif kind == "finished":
            msg = {"done": "Done", "stopped": "Stopped"}.get(val, "Error while typing")
            if val == "done":
                self.progress.set(1)
            self._finish_ui(msg)

    # ---- shutdown -----------------------------------------------------
    def _on_close(self):
        try:
            config.save({
                "wpm": int(round(self.wpm_var.get())),
                "typo_rate": round(float(self.typo_var.get()), 1),
                "revise_mode": MODES.get(self.mode_btn.get(), "random"),
                "always_on_top": bool(self.top_var.get()),
                "last_text": self._text(),
            })
        except Exception:
            pass
        if self.engine:
            self.engine.stop()
        self.hotkeys.stop()
        self.destroy()


def main():
    HumanTyperApp().mainloop()
