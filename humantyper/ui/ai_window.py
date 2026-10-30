"""AI WRITE window: generate text with a local model and hand it to the paste box."""
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from typing import Callable, Dict, Optional

import customtkinter as ctk

from .. import plat
from . import macutil
from .winutil import fit_to_workarea

from ..ai import engine as eng
from ..ai import models as mdl
from ..ai import prompts
from ..ai import settings as ai_settings
from . import theme as T
from .widgets import Panel, TextButton, Toggle

_window: Optional["AiWindow"] = None

INSTALL_MORE = "install more models..."


def open_ai_window(master, on_use_text: Callable[[str], None]) -> None:
    """Open the AI writer (or focus it if already open). on_use_text(text) replaces the paste box."""
    global _window
    if _window is not None:
        try:
            if _window.winfo_exists():
                _window.on_use_text = on_use_text
                _window.deiconify()
                _window.lift()
                _window.focus_force()
                return
        except tk.TclError:
            pass
        _window = None
    _window = AiWindow(master, on_use_text)


class AiWindow(ctk.CTkToplevel):
    def __init__(self, master, on_use_text):
        super().__init__(master, fg_color=T.BG)
        if T._family is None:
            T.init_fonts(self)
        self.on_use_text = on_use_text
        self.engine = eng.get_engine()
        self.cfg = self.engine.settings          # shared with the engine (force_cpu etc.)
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.models = []
        self.model_labels: Dict[str, str] = {}
        self.busy = False
        self.dl: Dict[str, dict] = {}          # catalog id -> {"cancel": Event, "done", "total", "speed"}
        self.dl_labels: Dict[str, tk.Label] = {}
        self.engine_text = "stopped"
        self.last_tps = 0.0
        self._closing = False

        self.title("Human Typer - AI WRITE")
        self.configure(bg=T.BG)
        self.geometry("700x700")
        self.minsize(600, 480)
        try:                                   # keep USE -> PASTE BOX on screen on small laptops
            fit_to_workarea(self, 700, 700, 600, 480, ctk.ScalingTracker.get_window_scaling(self), 0.7)
        except Exception:
            pass
        self._set_icon()
        self._build()
        self._refresh_models()
        self.engine.on_state = lambda s: self.events.put(("engine", s))
        self._sync_engine_text()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(40, self._poll)
        self.lift()
        self.focus_force()

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

    # ---- UI --------------------------------------------------------------
    def _build(self):
        f9, f10 = T.font(9), T.font(10)
        tk.Label(self, text="// local ai writer  ::  runs on this PC, nothing is sent anywhere //",
                 font=f9, fg=T.DIM, bg=T.BG, anchor="w").pack(fill="x", padx=14, pady=(10, 4))

        # model ----------------------------------------------------------
        mp = Panel(self, "MODEL", f9)
        mp.pack(fill="x", padx=10, pady=(2, 0))
        self.model_panel = mp
        row = tk.Frame(mp.body, bg=T.BG)
        row.pack(fill="x")
        row.columnconfigure(0, weight=1)
        self.model_var = tk.StringVar()
        self.model_menu = ctk.CTkOptionMenu(
            row, variable=self.model_var, values=[INSTALL_MORE], command=self._on_model_pick,
            font=ctk.CTkFont(family=T.font().actual("family"), size=T.scale_size(12)),
            dropdown_font=ctk.CTkFont(family=T.font().actual("family"), size=T.scale_size(12)),
            fg_color=T.INPUT_BG, button_color=T.LINE, button_hover_color=T.VIOLET,
            dropdown_fg_color=T.BG2, dropdown_hover_color=T.BG3, dropdown_text_color=T.TEXT,
            text_color=T.TEXT, corner_radius=0, height=26, dynamic_resizing=False)
        self.model_menu.grid(row=0, column=0, sticky="we", padx=(0, 10))
        self.models_btn = TextButton(row, "[ MODELS ]", self._toggle_manager, color=T.LAVENDER)
        self.models_btn.grid(row=0, column=1)
        self.status_lbl = tk.Label(mp.body, text="", font=f10, fg=T.ACCENT, bg=T.BG, anchor="w")
        self.status_lbl.pack(fill="x", pady=(5, 0))

        self.manager = Panel(self, "MODEL MANAGER", f9, color=T.VIOLET)
        self.manager_rows = tk.Frame(self.manager.body, bg=T.BG)
        self.manager_rows.pack(fill="x")
        foot = tk.Frame(self.manager.body, bg=T.BG)
        foot.pack(fill="x", pady=(6, 0))
        tk.Label(foot, text="downloads go to " + mdl.app_models_dir(), font=T.font(8), fg=T.DIM,
                 bg=T.BG, anchor="w").pack(side="left")
        TextButton(foot, "[ OPEN FOLDER ]", self._open_folder, fnt=T.font(8, True),
                   color=T.LAVENDER).pack(side="right")
        self.manager_open = False

        # prompt ---------------------------------------------------------
        pp = Panel(self, "PROMPT // write about...", f9)
        pp.pack(fill="x", padx=10, pady=(8, 0))
        self.prompt = ctk.CTkTextbox(
            pp.body, wrap="word", height=60, font=ctk.CTkFont(family=T.font().actual("family"), size=T.scale_size(13)),
            fg_color=T.INPUT_BG, text_color=T.TEXT, border_width=1, border_color=T.LINE, corner_radius=0,
            scrollbar_button_color=T.LINE, scrollbar_button_hover_color=T.VIOLET)
        self.prompt.pack(fill="x")
        self.prompt._textbox.configure(insertbackground=T.CYAN, insertwidth=2,
                                       selectbackground=T.VIOLET, selectforeground=T.WHITE)
        self.prompt.bind("<Control-Return>", lambda e: self._generate() or "break")

        opts = tk.Frame(pp.body, bg=T.BG)
        opts.pack(fill="x", pady=(8, 0))
        opts.columnconfigure(1, weight=1)
        tk.Label(opts, text="LENGTH", font=f10, fg=T.DIM, bg=T.BG, anchor="w", width=9).grid(row=0, column=0, sticky="w")
        length = int(self.cfg.get("length", 250))
        self.len_var = tk.DoubleVar(value=float(min(800, max(50, length))))
        ctk.CTkSlider(opts, from_=50, to=800, number_of_steps=75, variable=self.len_var,
                      command=lambda v: self._len_text(), height=14, corner_radius=0, button_corner_radius=0,
                      button_length=6, fg_color=T.BG3, progress_color=T.VIOLET, button_color=T.CYAN,
                      button_hover_color=T.PINK, bg_color=T.BG, border_width=0
                      ).grid(row=0, column=1, sticky="we", padx=(0, 10), pady=3)
        lf = tk.Frame(opts, bg=T.BG)
        lf.grid(row=0, column=2, sticky="w")
        self.len_entry = self._num_entry(lf, length)          # any number, the slider is just a shortcut
        self.len_entry.pack(side="left")
        tk.Label(lf, text=" words", font=f10, fg=T.DIM, bg=T.BG).pack(side="left")
        tk.Label(opts, text="LANGUAGE", font=f10, fg=T.DIM, bg=T.BG, anchor="w", width=9).grid(row=1, column=0, sticky="w", pady=2)
        lang = self.cfg.get("language", "auto")
        lang = prompts.LEGACY_LANG.get(lang, lang)
        self.lang_btn = Toggle(opts, list(prompts.LANGUAGES), lang if lang in prompts.LANGUAGES else "auto")
        self.lang_btn.grid(row=1, column=1, sticky="w", pady=2)
        tk.Label(opts, text="TONE", font=f10, fg=T.DIM, bg=T.BG, anchor="w", width=9).grid(row=2, column=0, sticky="w", pady=2)
        tone = self.cfg.get("tone", "neutral")
        self.tone_btn = Toggle(opts, list(prompts.TONES), tone if tone in prompts.TONES else "neutral")
        self.tone_btn.grid(row=2, column=1, sticky="w", pady=2)
        tk.Label(opts, text="REVISIONS", font=f10, fg=T.DIM, bg=T.BG, anchor="w", width=9).grid(row=3, column=0, sticky="w", pady=2)
        revs = str(self.cfg.get("revisions", "3"))
        self.rev_btn = Toggle(opts, list(prompts.REVISION_COUNTS), revs if revs in prompts.REVISION_COUNTS else "",
                              command=lambda v: self._set_entry(self.rev_entry, v))
        self.rev_btn.grid(row=3, column=1, sticky="w", pady=2)
        rf = tk.Frame(opts, bg=T.BG)
        rf.grid(row=3, column=2, sticky="w")
        self.rev_entry = self._num_entry(rf, revs)            # custom count; presets just fill it in
        self.rev_entry.pack(side="left")
        tk.Label(rf, text=" pairs", font=f10, fg=T.DIM, bg=T.BG).pack(side="left")
        self.rev_entry.bind("<KeyRelease>", lambda e: self.rev_btn.set(self.rev_entry.get().strip()))

        bar = tk.Frame(self, bg=T.BG)
        bar.pack(fill="x", padx=14, pady=(8, 0))
        self.gen_btn = TextButton(bar, "[ GENERATE ]", self._generate, fnt=T.font(12, True))
        self.gen_btn.pack(side="left")
        self.stop_btn = TextButton(bar, "[ STOP ]", self._stop, fnt=T.font(12, True), color=T.PINK)
        self.stop_btn.pack(side="left", padx=(12, 0))
        self.stop_btn.set_enabled(False)
        tk.Label(bar, text="Ctrl+Enter", font=T.font(8), fg=T.DIM, bg=T.BG).pack(side="left", padx=(12, 0))
        self.msg_lbl = tk.Label(self, text="", font=f10, fg=T.DIM, bg=T.BG, anchor="w", justify="left",
                                wraplength=680)
        self.msg_lbl.pack(fill="x", padx=14, pady=(4, 0))

        # output ---------------------------------------------------------
        op = Panel(self, "OUTPUT // editable", f9)
        op.pack(fill="both", expand=True, padx=10, pady=(6, 0))
        self.out = ctk.CTkTextbox(
            op.body, wrap="word", font=ctk.CTkFont(family=T.font().actual("family"), size=T.scale_size(13)),
            fg_color=T.INPUT_BG, text_color=T.TEXT, border_width=1, border_color=T.LINE, corner_radius=0,
            scrollbar_button_color=T.LINE, scrollbar_button_hover_color=T.VIOLET, height=140)
        self.out.pack(fill="both", expand=True)
        self.out._textbox.configure(insertbackground=T.CYAN, insertwidth=2,
                                    selectbackground=T.VIOLET, selectforeground=T.WHITE)
        self.out.bind("<<Modified>>", lambda e: None)

        bot = tk.Frame(self, bg=T.BG)
        bot.pack(fill="x", padx=14, pady=(8, 12), side="bottom", before=op)
        self.use_btn = TextButton(bot, "[ USE -> PASTE BOX ]", self._use, fnt=T.font(11, True))
        self.use_btn.pack(side="left")
        self.copy_btn = TextButton(bot, "[ COPY ]", self._copy, fnt=T.font(11, True), color=T.LAVENDER)
        self.copy_btn.pack(side="left", padx=(12, 0))
        self.clear_btn = TextButton(bot, "[ CLEAR ]", self._clear_out, fnt=T.font(11, True), color=T.DIM)
        self.clear_btn.pack(side="left", padx=(12, 0))
        self.count_lbl = tk.Label(bot, text="", font=f9, fg=T.DIM, bg=T.BG)
        self.count_lbl.pack(side="right")

    def _num_entry(self, master, value):
        e = tk.Entry(master, width=6, font=T.font(10, True), bg=T.INPUT_BG, fg=T.ACCENT, relief="flat",
                     insertbackground=T.CYAN, highlightthickness=1, highlightbackground=T.LINE,
                     highlightcolor=T.CYAN, justify="right")
        e.insert(0, str(value))
        return e

    @staticmethod
    def _set_entry(entry, value):
        entry.delete(0, "end")
        entry.insert(0, str(value))

    @staticmethod
    def _entry_int(entry, default, lo, hi):
        try:
            return max(lo, min(hi, int(float(entry.get().strip()))))
        except ValueError:
            return default

    def _len_text(self):
        self._set_entry(self.len_entry, int(round(self.len_var.get() / 10) * 10))

    def _words(self) -> int:
        return self._entry_int(self.len_entry, int(round(self.len_var.get() / 10) * 10), 10, 3000)

    def _revisions(self) -> int:
        return self._entry_int(self.rev_entry, 3, 0, 200)

    # ---- model list ------------------------------------------------------
    def _refresh_models(self):
        self.models = mdl.discover()
        self.model_labels = {}
        for m in self.models:
            tag = "" if m.source == "external" else " [app]"
            self.model_labels[f"{m.name}  ({mdl.human_size(m.size)}){tag}"] = m.path
        values = list(self.model_labels) + [INSTALL_MORE]
        self.model_menu.configure(values=values)
        want = self.cfg.get("model") or ""
        cur = next((l for l, p in self.model_labels.items() if os.path.normcase(p) == os.path.normcase(want)), None)
        if cur is None:
            d = mdl.pick_default(self.models)
            cur = next((l for l, p in self.model_labels.items() if d and p == d.path), None)
        if cur is None:
            self.model_var.set("(no model installed)")
        else:
            self.model_var.set(cur)
        self._render_manager()

    def _selected_path(self) -> Optional[str]:
        return self.model_labels.get(self.model_var.get())

    def _selected_name(self) -> str:
        p = self._selected_path()
        return next((m.name for m in self.models if m.path == p), "")

    def _on_model_pick(self, label):
        if label == INSTALL_MORE:
            self._refresh_models()
            self._open_manager(True)
            return
        if self.busy:
            self._refresh_models()
            return
        self.cfg["model"] = self.model_labels.get(label, "")
        self._save_cfg()
        self._sync_engine_text()

    def _sync_engine_text(self):
        p = self._selected_path()
        if self.engine.running() and p and os.path.normcase(self.engine.model_path) == os.path.normcase(p):
            self.engine_text = f"ready ({self.engine.mode})"
        elif not self.engine.running():
            self.engine_text = "stopped"
        else:
            self.engine_text = "stopped (another model loaded)"
        self._paint_status()

    def _paint_status(self):
        txt = f"engine: {self.engine_text}"
        if self.last_tps:
            txt += f"  ·  {self.last_tps:.1f} tok/s"
        col = T.ACCENT if "ready" in self.engine_text else (T.PINK if "loading" in self.engine_text else T.DIM)
        if not self.engine.available():
            txt, col = "engine: not installed", T.ERR
        self.status_lbl.configure(text=txt, fg=col)

    # ---- model manager ---------------------------------------------------
    def _toggle_manager(self):
        self._open_manager(not self.manager_open)

    def _open_manager(self, show):
        self.manager_open = show
        if show:
            self.manager.pack(fill="x", padx=10, pady=(8, 0), after=self.model_panel)
            self._render_manager()
        else:
            self.manager.pack_forget()

    def _render_manager(self):
        for w in self.manager_rows.winfo_children():
            w.destroy()
        self.dl_labels.clear()
        f9, f10 = T.font(9), T.font(10)
        for entry in mdl.CATALOG:
            have = mdl.installed_path(entry, self.models)
            row = tk.Frame(self.manager_rows, bg=T.BG)
            row.pack(fill="x", pady=2)
            row.columnconfigure(0, weight=1)
            head = tk.Frame(row, bg=T.BG)
            head.grid(row=0, column=0, sticky="w")
            tk.Label(head, text=entry.name, font=T.font(10, True), fg=T.ICE, bg=T.BG).pack(side="left")
            tk.Label(head, text=f"  {mdl.human_size(entry.size)}  ·  {entry.note}", font=f9, fg=T.DIM,
                     bg=T.BG).pack(side="left")
            side = tk.Frame(row, bg=T.BG)
            side.grid(row=0, column=1, sticky="e")
            st = self.dl.get(entry.id)
            if st:
                lbl = tk.Label(side, font=f9, fg=T.ACCENT, bg=T.BG, text=self._dl_text(st))
                lbl.pack(side="left", padx=(0, 8))
                self.dl_labels[entry.id] = lbl
                TextButton(side, "[ CANCEL ]", lambda e=entry: self._cancel_dl(e), fnt=f10, color=T.PINK).pack(side="left")
            elif have:
                where = "installed" if have.deletable else "installed (LM Studio)"
                tk.Label(side, text=f"✔ {where}", font=f9, fg=T.ACCENT, bg=T.BG).pack(side="left", padx=(0, 8))
                if have.deletable:
                    TextButton(side, "[ DELETE ]", lambda m=have: self._delete(m), fnt=f10, color=T.PINK).pack(side="left")
            else:
                part = os.path.join(mdl.app_models_dir(), entry.filename + ".part")
                label = "[ RESUME ]" if os.path.exists(part) else "[ INSTALL ]"
                TextButton(side, label, lambda e=entry: self._install(e), fnt=f10).pack(side="left")
        if not self.dl:
            tk.Label(self.manager_rows, text="Models are downloaded from huggingface.co over HTTPS "
                     "and run only on this PC.", font=T.font(8), fg=T.DIM, bg=T.BG, anchor="w").pack(fill="x", pady=(4, 0))

    @staticmethod
    def _dl_text(st):
        total = st.get("total") or 1
        done = st.get("done", 0)
        return (f"{T.ascii_bar(done, 0, total, 14)} {done * 100 // total:>3d}%  "
                f"{st.get('speed', 0) / 1e6:.1f} MB/s")

    def _install(self, entry):
        if entry.id in self.dl:
            return
        st = {"cancel": threading.Event(), "done": 0, "total": entry.size, "speed": 0.0}
        self.dl[entry.id] = st

        def work():
            err = ""
            try:
                mdl.install(entry, progress=lambda d, t, s: self.events.put(("dl", entry.id, d, t, s)),
                            cancel=st["cancel"])
            except mdl.DownloadCancelled:
                err = "cancelled"
            except Exception as e:
                err = str(e) or e.__class__.__name__
            self.events.put(("dl_done", entry.id, err))

        threading.Thread(target=work, daemon=True).start()
        self._render_manager()

    def _cancel_dl(self, entry):
        st = self.dl.get(entry.id)
        if st:
            st["cancel"].set()

    def _delete(self, m):
        if self.engine.running() and os.path.normcase(self.engine.model_path) == os.path.normcase(m.path):
            self.engine.stop()
        if mdl.delete_model(m):
            if os.path.normcase(self.cfg.get("model", "")) == os.path.normcase(m.path):
                self.cfg["model"] = ""
            self._info(f"deleted {m.name}")
        else:
            self._info(f"could not delete {m.name}", T.ERR)
        self._refresh_models()
        self._sync_engine_text()

    def _open_folder(self):
        d = mdl.app_models_dir()
        os.makedirs(d, exist_ok=True)
        try:
            if plat.IS_MAC:
                subprocess.run(["open", d], check=False)
            elif plat.IS_WIN:
                os.startfile(d)
            else:
                subprocess.run(["xdg-open", d], check=False)
        except Exception:
            pass

    # ---- generation ------------------------------------------------------
    def _info(self, text, color=None):
        self.msg_lbl.configure(text=text, fg=color or T.DIM)

    def _set_busy(self, busy):
        self.busy = busy
        self.gen_btn.set_enabled(not busy)
        self.stop_btn.set_enabled(busy)
        self.use_btn.set_enabled(not busy)          # text isn't final until the revision pairs are in
        self.copy_btn.set_enabled(not busy)
        self.model_menu.configure(state="disabled" if busy else "normal")

    def _generate(self):
        if self.busy:
            return
        prompt = self.prompt.get("1.0", "end").strip()
        if not prompt:
            self._info("Write what the text should be about first.", T.PINK)
            return
        if not self.engine.available():
            self._info(eng.MISSING_MSG, T.ERR)
            self._paint_status()
            return
        path = self._selected_path()
        if not path:
            self._info("No model installed yet. Pick one in the model manager below and press INSTALL.", T.PINK)
            self._open_manager(True)
            return
        words = self._words()
        msgs = prompts.build_messages(prompt, words, self.lang_btn.get(), self.tone_btn.get(),
                                      self._selected_name())
        self.cfg.update(length=words, language=self.lang_btn.get(), tone=self.tone_btn.get(), model=path,
                        revisions=str(self._revisions()))
        self._save_cfg()
        self.out.delete("1.0", "end")
        self.last_tps = 0.0
        self._set_busy(True)
        self._info("working...", T.DIM)
        self._cancelled = False
        job = (self._revisions(), self.lang_btn.get(), self._selected_name())
        threading.Thread(target=self._worker, args=(path, msgs, words, job), daemon=True).start()

    def _worker(self, path, msgs, words, job=(0, "auto", "")):
        q = self.events
        try:
            self.engine.ensure_started(path)
            q.put(("engine", f"ready ({self.engine.mode})"))
            q.put(("info", "generating..."))
            t0, n, parts = None, 0, []
            for piece in self.engine.generate(msgs, prompts.max_tokens_for(words)):
                if t0 is None:
                    t0 = time.monotonic()
                n += 1
                parts.append(piece)
                q.put(("chunk", piece, n / max(time.monotonic() - t0, 0.3)))
            tps = self.engine.last_tps
            nrev, lang, name = job
            if nrev > 0 and not self._cancelled:
                self._add_revisions("".join(parts).strip(), nrev, lang, name)
            q.put(("done", tps, ""))
        except eng.EngineError as e:
            q.put(("done", 0.0, f"{e.kind}:{e}"))
        except Exception as e:
            q.put(("done", 0.0, f"server:{e}"))

    def _add_revisions(self, text, nrev, lang, name):
        """Ask the model for a rough first draft of a few sentences and insert [draft][final] pairs."""
        q = self.events
        spans = prompts.pick_spans(text, nrev)
        pairs = []
        for i, (a, b) in enumerate(spans, 1):
            if self._cancelled:
                return
            q.put(("info", f"adding revisions {i}/{len(spans)}... (please wait, USE unlocks when done)"))
            original = text[a:b]
            draft = None
            for temp in (0.8, 1.0):                      # model draft, one retry, then a built-in cut
                raw = "".join(self.engine.generate(prompts.draft_messages(original, lang, name),
                                                   prompts.max_tokens_for(len(original.split()) + 10),
                                                   temperature=temp))
                draft = prompts.clean_draft(raw, original)
                if draft or self._cancelled:
                    break
            draft = draft or prompts.fallback_draft(original)
            if draft:
                pairs.append((a, b, draft))
        self._revs_wanted = nrev
        if pairs and not self._cancelled:
            q.put(("replace", prompts.apply_revisions(text, pairs), len(pairs)))

    def _ev_replace(self, text, k):
        self.out.delete("1.0", "end")
        self.out.insert("1.0", text)
        self._revs_added = k

    def _stop(self):
        if self.busy:
            self._cancelled = True
            self.engine.cancel()
            self._info("stopping...", T.DIM)

    # ---- event pump ------------------------------------------------------
    def _poll(self):
        if self._closing:
            return
        try:
            while True:
                ev = self.events.get_nowait()
                getattr(self, "_ev_" + ev[0])(*ev[1:])
        except queue.Empty:
            pass
        except tk.TclError:
            return
        self.after(40, self._poll)

    def _ev_engine(self, text):
        self.engine_text = text
        self._paint_status()

    def _ev_info(self, text):
        self._info(text)

    def _ev_chunk(self, piece, tps):
        self.out.insert("end", piece)
        self.out.see("end")
        self.last_tps = tps
        self._paint_status()
        self._update_count()

    def _ev_done(self, tps, err):
        self._set_busy(False)
        if tps:
            self.last_tps = tps
        if err:
            kind, _, msg = err.partition(":")
            self._info(msg, T.ERR)
            if kind == "oom":
                self._info(msg + "  Try a smaller model (MODELS).", T.ERR)
                self._open_manager(True)
            elif kind == "missing":
                self._paint_status()
            self.engine_text = "stopped" if not self.engine.running() else self.engine_text
        else:
            text = self.out.get("1.0", "end").strip()
            k = getattr(self, "_revs_added", 0)
            want = getattr(self, "_revs_wanted", 0)
            self._revs_added = self._revs_wanted = 0
            extra = f" with {k} [draft][final] revision{'s' if k != 1 else ''}" if k else ""
            if want and k < want:
                extra += f" (asked for {want}; the text only had {k} sentence parts to revise)"
            self._info(f"done - {len(text.split())} words{extra}. Edit if you like, then USE -> PASTE BOX.", T.ACCENT)
        self._paint_status()
        self._update_count()

    def _ev_dl(self, cid, done, total, speed):
        st = self.dl.get(cid)
        if st is not None:
            st.update(done=done, total=total or st["total"], speed=speed)
            lbl = self.dl_labels.get(cid)
            if lbl is not None:
                lbl.configure(text=self._dl_text(st))

    def _ev_dl_done(self, cid, err):
        self.dl.pop(cid, None)
        entry = next((e for e in mdl.CATALOG if e.id == cid), None)
        if err and err != "cancelled":
            self._info(f"download failed: {err}", T.ERR)
        elif err:
            self._info("download cancelled (partial file kept, press RESUME to continue).", T.DIM)
        elif entry:
            self._info(f"installed {entry.name}", T.ACCENT)
        self._refresh_models()
        self._sync_engine_text()

    # ---- output actions --------------------------------------------------
    def _update_count(self):
        t = self.out.get("1.0", "end").strip()
        self.count_lbl.configure(text=f"{len(t.split())} words" if t else "")

    def _text(self) -> str:
        return self.out.get("1.0", "end").strip()

    def _use(self):
        t = self._text()
        if not t:
            self._info("Nothing to use yet - generate some text first.", T.PINK)
            return
        self.on_use_text(t)
        self._info("sent to the paste box.", T.ACCENT)

    def _copy(self):
        t = self._text()
        if t:
            self.clipboard_clear()
            self.clipboard_append(t)
            self._info("copied to clipboard.", T.ACCENT)

    def _clear_out(self):
        if not self.busy:
            self.out.delete("1.0", "end")
            self._update_count()

    # ---- lifecycle -------------------------------------------------------
    def _save_cfg(self):
        try:
            ai_settings.save(self.cfg)
        except Exception:
            pass

    def _close(self):
        global _window
        self._closing = True
        self.engine.cancel()
        for st in self.dl.values():
            st["cancel"].set()
        self.engine.on_state = None
        try:
            self.cfg.update(length=self._words(), language=self.lang_btn.get(), tone=self.tone_btn.get())
            ai_settings.save(self.cfg)
        except Exception:
            pass
        _window = None
        self.destroy()
