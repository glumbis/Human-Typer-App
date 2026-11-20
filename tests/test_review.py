"""REVIEW FIELD: edit spans, field grab, engine edit mode, F10 hotkey."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random

import pytest

from humantyper import hotkeys as hk
from humantyper.backends import FakeBackend
from humantyper.engine import Settings, TypingEngine
from humantyper.review import (MAX_CHARS, NOTHING_COPIED, apply_edits, compute_edits, grab_field,
                               improved_spans, resolve_model)


# ---- edit spans ------------------------------------------------------------------------------
PAIRS = [
    ("I like cats.", "I really like cats."),                                   # insertion
    ("I really really like cats.", "I really like cats."),                     # deletion
    ("Teh cat sat on teh mat.", "The cat sat on the mat."),                    # two word fixes
    ("Jeg liker å gå tur i skogen. Det er fint vær.",
     "Jeg elsker å gå tur i skogen. Det er nydelig vær i dag."),               # Norwegian æøå
    ("Første avsnitt her.\n\nAndre avsnittet er kort.", "Første avsnitt her.\n\nDet andre avsnittet er kort og godt."),
    ("hello", "Hello, world!"),
    ("a b c d e f g", "a B c d e F g"),
    ("Something, completely different.", "Nothing at all is the same here!"),
    ("Line one\nline two\n", "Line one.\nLine two.\n"),
    ("trailing space ", "trailing space"),
    ("Double  space here.", "Double space here."),
]


@pytest.mark.parametrize("orig,new", PAIRS)
def test_compute_edits_round_trip_and_shape(orig, new):
    edits = compute_edits(orig, new)
    assert apply_edits(orig, edits) == new
    last = 0
    for s, e, rep in edits:
        assert last <= s <= e <= len(orig)
        last = e
        assert any(c.isalnum() for c in orig[s:e])            # always a real phrase to locate
    spans = improved_spans(edits)
    assert [new[a:b] for a, b in spans] == [rep for _s, _e, rep in edits]


def test_compute_edits_no_change():
    assert compute_edits("Same text.", "Same text.") == []
    assert compute_edits("", "") == []


def test_compute_edits_examples():
    assert compute_edits("I like cats.", "I really like cats.") == [(0, 1, "I really")]
    assert compute_edits("Teh cat sat on teh mat.", "The cat sat on the mat.") == [(0, 3, "The"), (15, 18, "the")]
    # changes two words apart merge into one edit, far apart ones do not
    e = compute_edits("one two three four five six seven", "ONE two three FOUR five six seven")
    assert e == [(0, 18, "ONE two three FOUR")]
    e = compute_edits("one two three four five six seven", "ONE two three four five six SEVEN")
    assert len(e) == 2
    # never across a line break
    e = compute_edits("aa\nbb", "AA\nBB")
    assert e == [(0, 2, "AA"), (3, 5, "BB")]
    # punctuation-only change widened to its word
    assert compute_edits("Hello, world.", "Hello; world.") == [(0, 6, "Hello;")]
    # Norwegian letters are word characters: the whole word is retyped
    assert compute_edits("Det er fin vaer.", "Det er fint vær.") == [(7, 15, "fint vær")]


def test_compute_edits_random_round_trip():
    rng = random.Random(3)
    words = "the cat dog æøå på gå er fint , . ! \n a b".split(" ")
    for _ in range(300):
        a = " ".join(rng.choice(words) for _ in range(rng.randint(0, 12)))
        b = " ".join(rng.choice(words) for _ in range(rng.randint(0, 12)))
        assert apply_edits(a, compute_edits(a, b)) == b


# ---- grab ------------------------------------------------------------------------------------
def test_grab_via_clipboard_restores_clipboard_and_caret_at_end():
    fb = FakeBackend("My own text, written by me.", clipboard_initial="user clip")
    fb.cursor = 5
    g = grab_field(fb, sleep=lambda s: None)
    assert g.ok and g.text == "My own text, written by me." and g.method == "clipboard"
    assert fb.cursor == len(fb.buf) and fb.sel is None              # collapsed at the end
    assert fb.clipboard == "user clip" and fb.clip_restored == 1
    assert fb.text == "My own text, written by me."


def test_grab_crlf_clipboard_normalized():
    fb = FakeBackend("a\nb", crlf_clipboard=True)
    g = grab_field(fb, sleep=lambda s: None)
    assert g.text == "a\nb"


def test_grab_nothing_copied_collapses_selection_and_restores():
    fb = FakeBackend("text", clipboard_works=False, clipboard_initial="keep")
    g = grab_field(fb, sleep=lambda s: None)
    assert not g.ok and g.error == NOTHING_COPIED
    assert fb.sel is None and fb.cursor == 4 and fb.clipboard == "keep"


def test_grab_empty_and_too_long():
    assert "empty" in grab_field(FakeBackend("   "), sleep=lambda s: None).error
    g = grab_field(FakeBackend("x" * (MAX_CHARS + 1)), sleep=lambda s: None)
    assert not g.ok and "too long" in g.error and str(MAX_CHARS) in g.error


def test_grab_prefers_locator_and_leaves_clipboard_alone():
    fb = FakeBackend("read by UIA", locator_available=True, clipboard_initial="c")
    fb.cursor = 2
    g = grab_field(fb, sleep=lambda s: None)
    assert g.ok and g.text == "read by UIA" and g.method == "UIA"
    assert fb.clip_restored == 0 and fb.clipboard == "c" and fb.cursor == len(fb.buf)
    fb2 = FakeBackend("no text pattern", locator_available=False)
    assert grab_field(fb2, sleep=lambda s: None).method == "clipboard"


def test_resolve_model_saved_or_default():
    class M:
        def __init__(self, path, name):
            self.path, self.name, self.size, self.source = path, name, 3e9, "app"
    ms = [M("/m/a.gguf", "A"), M("/m/b.gguf", "B")]
    assert resolve_model({"model": "/m/b.gguf"}, ms) == ("/m/b.gguf", "B")
    path, name = resolve_model({"model": "/gone.gguf"}, ms)
    assert path in ("/m/a.gguf", "/m/b.gguf")
    assert resolve_model({}, []) == (None, "")


# ---- engine edit mode ------------------------------------------------------------------------
def run_edit(original, improved, mode="count", fb=None, typo=0.0, seed=1):
    fb = fb or FakeBackend(original)
    fb.cursor = 3 if len(fb.buf) > 3 else 0             # the user clicked somewhere into the field
    logs = []
    eng = TypingEngine(fb, Settings(typo_rate=typo, seed=seed, locate_mode=mode), sleep=lambda s: None,
                       on_log=logs.append)
    ok = eng.run_edit(original, compute_edits(original, improved))
    return fb, eng, logs, ok


@pytest.mark.parametrize("mode", ["count", "clipboard", "auto"])
@pytest.mark.parametrize("orig,new", PAIRS)
def test_engine_edit_mode_end_to_end(mode, orig, new):
    fb, eng, logs, ok = run_edit(orig, new, mode)
    assert ok and fb.text == new, logs
    assert fb.errors == []
    n = len(compute_edits(orig, new))
    assert eng.edit_result == (n, n)
    assert f"applied {n} of {n} edit{'s' if n != 1 else ''}" in logs


@pytest.mark.parametrize("mode", ["count", "clipboard"])
def test_engine_edit_mode_with_typos(mode):
    orig = "This is my frist draft of the text. It has a few erors in it, and its not grate."
    new = "This is my first draft of the text. It has a few errors in it, and it's not great."
    for seed in range(5):
        fb, eng, logs, ok = run_edit(orig, new, mode, typo=0.08, seed=seed)
        assert ok and fb.text == new, logs


def test_engine_edit_mode_uia():
    orig, new = "Jeg liker å gå tur. Det er fint.", "Jeg elsker å gå tur. Det er nydelig."
    fb = FakeBackend(orig, locator_available=True)
    fb2, eng, logs, ok = run_edit(orig, new, "auto", fb=fb)
    assert ok and fb.text == new
    assert any("locate: UIA ✓" in l for l in logs)


def test_engine_edit_mode_clipboard_restored():
    orig, new = "one two three four five six seven eight", "one TWO three four five six seven EIGHT"
    fb = FakeBackend(orig, clipboard_initial="mine")
    run_edit(orig, new, "clipboard", fb=fb)
    assert fb.text == new and fb.clipboard == "mine"


def test_engine_edit_mode_autocorrect_drift():
    """The app changes what we type (an extra '!' after "go"): later edits still land right,
    because every edit is located from the real end of the text."""
    def ac(fb):
        if fb.text[:fb.cursor].endswith("go"):
            fb.buf.insert(fb.cursor, "!")
            fb.cursor += 1
    orig = "we will goo there soon and then we stay a long while at the lake house, tomorow."
    new = "we will go there soon and then we stay a long while at the lake house, tomorrow."
    for mode in ("count", "clipboard"):
        fb = FakeBackend(orig, autocorrect=ac)
        fb, eng, logs, ok = run_edit(orig, new, mode, fb=fb)
        assert ok and fb.errors == []
        assert fb.text == new.replace("go ", "go! ", 1), (mode, fb.text)


def test_engine_edit_mode_skips_unverifiable_edit():
    """The field no longer holds the phrase: clipboard mode skips it, nothing deleted blindly."""
    orig, new = "alpha beta gamma delta epsilon zeta eta theta", "alpha BETA gamma delta epsilon zeta eta THETA"
    fb = FakeBackend(orig.replace("beta", "bxta"))
    fb, eng, logs, ok = run_edit(orig, new, "clipboard", fb=fb)
    assert ok and fb.text == "alpha bxta gamma delta epsilon zeta eta THETA"
    assert eng.edit_result == (1, 2)
    assert any("applied 1 of 2 edits (1 skipped" in l for l in logs)


def test_engine_edit_mode_count_refuses_changed_field():
    orig, new = "first text here", "first TEXT here"
    fb = FakeBackend("something else entirely", locator_available=True)
    fb, eng, logs, ok = run_edit(orig, new, "count", fb=fb)
    assert not ok and fb.text == "something else entirely"
    assert any("cannot verify" in l for l in logs)


def test_engine_edit_mode_rejects_bad_spans():
    eng = TypingEngine(FakeBackend("abc"), Settings(), sleep=lambda s: None)
    with pytest.raises(ValueError):
        eng.run_edit("abc", [(1, 5, "x")])
    with pytest.raises(ValueError):
        eng.run_edit("abcdef", [(0, 3, "x"), (2, 4, "y")])


def test_engine_edit_mode_progress_and_thread():
    orig, new = "a small mistakke here", "a small mistake here"
    fb = FakeBackend(orig)
    prog, done = [], []
    import threading
    ev = threading.Event()
    eng = TypingEngine(fb, Settings(typo_rate=0, seed=1, locate_mode="count"), sleep=lambda s: None,
                       on_progress=prog.append, on_finish=lambda r: (done.append(r), ev.set()))
    eng.start_edit(orig, compute_edits(orig, new))
    assert ev.wait(10) and done == ["done"] and fb.text == new
    assert prog and prog[-1] == 1.0
    eng.run("plain [a][b]")                       # a normal run afterwards is unaffected
    assert fb.text.endswith("plain b")


def test_engine_edit_mode_deletions_only_progress():
    orig, new = "keep this very very text", "keep this very text"
    prog = []
    fb = FakeBackend(orig)
    eng = TypingEngine(fb, Settings(typo_rate=0, seed=1, locate_mode="count"), sleep=lambda s: None,
                       on_progress=prog.append)
    assert eng.run_edit(orig, compute_edits(orig, new)) and fb.text == new


def test_stats_counts_edit_tags():
    from humantyper.ui.stats import Stats
    st = Stats()
    st.begin(10)
    st.on_log("EDIT 2/5 // locate: count (unverified)")
    assert st.rev_total == 5 and st.rev_done == 1


# ---- F10 hotkey ------------------------------------------------------------------------------
def test_f10_windows_handle():
    hits = []
    g = hk.GlobalHotkeys(lambda: hits.append(8), lambda: hits.append(9), on_f10=lambda: hits.append(10))
    assert g.handle(0x100, hk.VK_F10, 0) is True and hits == [10]
    assert g.handle(0x100, hk.VK_F10, 0) is True and hits == [10]          # auto-repeat ignored
    assert g.handle(0x101, hk.VK_F10, 0) is True
    assert g.handle(0x100, hk.VK_F10, hk.LLKHF_INJECTED) is False
    assert g.handle(0x100, hk.VK_F8, 0) and hits == [10, 8]
    g2 = hk.GlobalHotkeys(lambda: None, lambda: None)                        # without on_f10: passes
    assert g2.handle(0x100, hk.VK_F10, 0) is False


def test_f10_bypasses_keyboard_guard():
    kg = hk.KeyGuard(True)
    kg.engine_state("start")                    # blocking
    hits = []
    g = hk.GlobalHotkeys(lambda: None, lambda: None, guard=kg, on_f10=lambda: hits.append(10))
    assert g.handle(0x100, hk.VK_F10, 0) and hits == [10] and kg.blocked == 0


def test_f10_mac_handle():
    hits = []
    g = hk.GlobalHotkeys(lambda: None, lambda: None, on_f10=lambda: hits.append(10))
    held = set()
    assert hk.mac_handle(held, g._mac_callbacks, hk.MAC_KEYDOWN, hk.MAC_KC_F10) is True and hits == [10]
    assert hk.mac_handle(held, g._mac_callbacks, hk.MAC_KEYDOWN, hk.MAC_KC_F10, autorepeat=1) and hits == [10]
    assert hk.mac_handle(held, g._mac_callbacks, hk.MAC_KEYUP, hk.MAC_KC_F10) is True and not held
    assert hk.mac_handle(held, g._mac_callbacks, hk.MAC_KEYDOWN, hk.MAC_KC_F10, src_pid=5, own_pid=5) is False
    assert hk.MAC_KC_F10 == 109 and hk.VK_F10 == 0x79
