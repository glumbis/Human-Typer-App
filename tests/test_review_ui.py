"""REVIEW FIELD GUI: the review window and the whole app flow (F10 -> grab -> AI -> APPLY -> F8)
with a FakeBackend and a fake AI engine. Skipped without a display."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time

import pytest

from humantyper.backends import FakeBackend
from humantyper.engine import TypingEngine


@pytest.fixture(scope="module")
def appw():
    """One HumanTyperApp for the module (several Tk interpreters in one process are flaky on
    Windows: init.tcl sometimes cannot be read for the third one)."""
    import humantyper.app as app
    from humantyper.ui import theme as T
    mp = pytest.MonkeyPatch()
    mp.setattr(app.config, "save", lambda *a, **k: None)
    mp.setattr(app.config, "load", lambda *a, **k: dict(app.config.DEFAULTS))
    mp.setattr(app.GlobalHotkeys, "start", lambda self: None)
    T._fonts.clear()                # cached fonts may belong to the Tk of an earlier test module
    try:
        w = app.HumanTyperApp()
    except Exception as e:      # no display available
        mp.undo()
        pytest.skip(f"no display: {e}")
    yield app, w
    try:
        w._on_close()
    finally:
        mp.undo()


def _pump(w, cond, timeout=10.0):
    t_end = time.time() + timeout
    while time.time() < t_end:
        w.update()
        if cond():
            return True
        time.sleep(0.02)
    return False


class FakeAi:
    def __init__(self, answer):
        self.settings = {"improve_style": "fix", "language": "auto", "model": ""}
        self.answer = answer
        self.calls = []
        self.cancelled = False

    def available(self):
        return True

    def generating(self):
        return False

    def ensure_started(self, path):
        self.calls.append(("start", path))

    def generate(self, msgs, max_tokens=0, temperature=0.5):
        self.calls.append(("gen", msgs[-1]["content"]))
        yield self.answer

    def cancel(self):
        self.cancelled = True


def test_review_window_construct_highlight_apply_close(appw):
    _app_mod, root = appw
    from humantyper.ui.review_window import ReviewWindow
    got, redo, closed = [], [], []
    w = ReviewWindow(root, "Teh cat sat on teh mat.", "The cat sat on the mat.", "fix",
                     on_apply=lambda t: got.append(t) or "armed?", on_redo=redo.append,
                     on_close=lambda: closed.append(1), message="2 edits suggested")
    root.update()
    assert w.n_edits == 2 and w.count_lbl.cget("text") == "2 edits"
    assert w.old.tag_ranges("old") and w.new._textbox.tag_ranges("new")
    w.new.insert("end", " Really.")
    w._highlight()
    r = w.new._textbox.tag_ranges("new")
    assert w.n_edits == 2 and "Really" in w.new._textbox.get(r[-2], r[-1])   # merged with "the mat."
    w._apply()
    assert got == ["The cat sat on the mat. Really."] and w.msg_lbl.cget("text") == "armed?"
    w.style_btn.set("formal")
    w._redo()
    assert redo == ["formal"] and w.busy and not w.apply_btn.enabled
    w.set_result("The cat sat on the mat.", "again")
    assert not w.busy and w.n_edits == 2
    w.cancel()
    assert closed == [1] and w._closed
    root.update()


def test_app_review_flow_end_to_end(appw, monkeypatch):
    app, w = appw
    original = "Teh cat sat on teh mat. It was a nice day for it."
    improved = "The cat sat on the mat. It was a nice day for it."
    fb = FakeBackend(original, clipboard_initial="clip")
    fb.cursor = 4
    w.backend = fb
    ai = FakeAi(improved)
    import humantyper.ai.engine as ai_eng
    import humantyper.ai.settings as ai_settings
    monkeypatch.setattr(ai_eng, "get_engine", lambda: ai)
    monkeypatch.setattr(ai_settings, "save", lambda cfg: None)
    monkeypatch.setattr(app.review, "resolve_model", lambda cfg, models=None: ("/m/x.gguf", "X"))
    monkeypatch.setattr(w, "_own_window_front", lambda: False)

    class FastEngine(TypingEngine):
        def __init__(self, *a, **k):
            k["sleep"] = lambda s: None
            super().__init__(*a, **k)
    monkeypatch.setattr(app, "TypingEngine", FastEngine)

    w.locate_btn.set("clipboard")
    w._handle("hotkey", "f10")
    assert _pump(w, lambda: w._review_win is not None), w._status_text
    assert fb.clipboard == "clip" and fb.text == original           # read only, clipboard back
    assert ai.calls[0] == ("start", "/m/x.gguf")
    rw = w._review_win
    assert rw.n_edits == 2 and rw.improved() == improved
    assert rw.style() == "fix"
    assert rw.on_apply(rw.improved()) is None                       # APPLY IN FIELD
    assert w._edit_job and len(w._edit_job["edits"]) == 2
    assert w._review_win is None and "EDITS READY (2)" in w._status_text
    assert w._overlay_state() == "EDITS" and w.stop_btn.enabled
    fb.cursor = 10                                                  # user clicks into the field
    w._handle("hotkey", "f8")
    assert _pump(w, lambda: w.st == "idle" and w.engine is None, 20), w._status_text
    assert fb.text == improved and fb.errors == []
    assert w._status_text.startswith("DONE")
    assert any("applied 2 of 2 edits" in t for _s, t, _g in w._log_lines)
    assert w._edit_job is None and w._run_mode == "type"


def test_app_review_refusals(appw, monkeypatch):
    app, w = appw
    monkeypatch.setattr(w, "_own_window_front", lambda: True)
    w._handle("hotkey", "f10")
    assert _pump(w, lambda: w._review is None)
    assert "click into your text field first" in w._status_text
    # nothing could be copied
    monkeypatch.setattr(w, "_own_window_front", lambda: False)
    w.backend = FakeBackend("text", clipboard_works=False)
    w._handle("hotkey", "f10")
    assert _pump(w, lambda: w._review is None)
    assert "nothing was copied" in w._status_text and w.backend.sel is None
    # no AI model
    w.backend = FakeBackend("some words written here")
    ai = FakeAi("x")
    import humantyper.ai.engine as ai_eng
    monkeypatch.setattr(ai_eng, "get_engine", lambda: ai)
    monkeypatch.setattr(app.review, "resolve_model", lambda cfg, models=None: (None, ""))
    w._handle("hotkey", "f10")
    assert _pump(w, lambda: w._review is None)
    assert "No AI model installed" in w._status_text
    # button countdown, then STOP cancels it
    w._review_button()
    assert w._review == "countdown" and "T-3" in w._status_text
    w._stop()
    assert w._review is None and "cancelled" in w._status_text


def test_main_window_resume_counts_down(appw):
    app, w = appw
    class Eng:
        paused = True
        resumed = 0

        def resume(self):
            self.resumed += 1
            self.paused = False

        def stop(self):
            pass
    eng = Eng()
    w.st, w.engine = "typing", eng
    w._pause_button()
    assert w._resume_id is not None and "RESUMING — T-3" in w._status_text and eng.resumed == 0
    w._pause_button()                                   # clicked again: cancelled, still paused
    assert w._resume_id is None and eng.resumed == 0 and "PAUSED" in w._status_text
    w._pause_button()
    w._resume_countdown(0, eng)                         # countdown elapsed
    assert eng.resumed == 1
    eng.paused = True
    w._pause_button()
    w._toggle_pause()                                   # F9 during the countdown: at once
    assert w._resume_id is None and eng.resumed == 2
    w.st, w.engine = "idle", None
