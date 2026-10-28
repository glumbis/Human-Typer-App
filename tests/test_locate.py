import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from humantyper.backends import FakeBackend, FakeLocator
from humantyper.engine import Settings, TypingEngine
from humantyper.locator import choose_occurrence, normalize
from humantyper.parser import parse


def run(text, mode="auto", fb=None, seed=1, typo=0.0, rev="end", **kw):
    fb = fb or FakeBackend()
    logs, chars = [], []
    eng = TypingEngine(fb, Settings(typo_rate=typo, revise_mode=rev, seed=seed, locate_mode=mode),
                       sleep=lambda s: None, on_log=logs.append, on_char=chars.append, **kw)
    ok = eng.run(text)
    return fb, logs, chars, ok


def teh_autocorrect(fb):
    """Autocorrect 'teh ' -> 'the ' and swallow a space after '. ' capitalisation drift."""
    t = fb.text
    if t.endswith("teh "):
        fb.buf[-4:] = list("the ")
    if t.endswith("..") :
        pass


def extra_char_autocorrect(fb):
    """Typing a space after 'colour' inserts an extra '!' (changes real distances)."""
    if fb.text[:fb.cursor].endswith("go "):
        fb.buf.insert(fb.cursor - 1, "!")
        fb.cursor += 1


# ---- pure helpers -----------------------------------------------------------
def test_choose_occurrence_closest_and_ambiguous():
    t = "cat dog cat dog cat"
    assert choose_occurrence(t, "cat", 0) == len(t)
    assert choose_occurrence(t, "cat", 8) == 11          # middle one: 8 chars after its end
    assert choose_occurrence(t, "cow", 0) is None
    assert choose_occurrence("abab", "ab", 1) is None    # ends 2 and 4 -> dists 2,0 vs expected 1: tie
    assert choose_occurrence("abc", "", 0) == 0


def test_normalize():
    assert normalize("a\r\nb\rc\nd") == "a\nb\nc\nd"


# ---- B: clipboard ---------------------------------------------------------
def test_b_basic_and_clipboard_restored():
    fb = FakeBackend(clipboard_initial="user data")
    fb, logs, chars, ok = run("I [realy][really] like it, a lot.", fb=fb)
    assert ok and fb.text == "I really like it, a lot."
    assert fb.cursor == len(fb.buf) and fb.errors == []
    assert fb.clipboard == "user data" and fb.clip_restored >= 1
    assert any("locate: clipboard" in l and "REV 1/1" in l for l in logs)


def test_b_recovers_from_autocorrect_drift_where_count_fails():
    # 'teh ' -> 'the ' keeps length, so drift comes from the extra '!' inserted after "go "
    text = "[xx][yy] please go now and then go again, teh end"
    fb_c, _, _, _ = run(text, mode="count", fb=FakeBackend(autocorrect=extra_char_autocorrect))
    fb_b, logs, _, _ = run(text, mode="clipboard", fb=FakeBackend(autocorrect=extra_char_autocorrect))
    assert fb_b.text.startswith("yy please go! now and then go! again")
    assert not fb_c.text.startswith("yy please")      # counting desynced and edited the wrong spot
    assert fb_b.cursor == len(fb_b.buf)


def test_b_drift_with_length_changing_autocorrect_in_middle():
    def ac(fb):
        if fb.text.endswith("dont "):
            fb.buf[-5:] = list("don't ")
            fb.cursor = len(fb.buf)
    fb, logs, _, _ = run("[old][new] I dont know, you dont care and stuff.", mode="clipboard",
                         fb=FakeBackend(autocorrect=ac))
    assert fb.text.startswith("new I don't know") and fb.cursor == len(fb.buf)


def test_b_duplicate_phrase_picks_expected_occurrence():
    text = "the cat sat. [the cat][A dog] ran. the cat left. the cat slept."
    fb, logs, _, _ = run(text)
    assert fb.text == "the cat sat. A dog ran. the cat left. the cat slept."


def test_b_duplicate_with_drift():
    def ac(fb):
        if fb.text.endswith("..."):
            fb.buf[-3:] = list("…")
            fb.cursor = len(fb.buf)
    text = "foo... [foo][bar]... foo... tail text here"
    fb, logs, _, _ = run(text, mode="clipboard", fb=FakeBackend(autocorrect=ac))
    assert fb.text == "foo… bar… foo… tail text here" or fb.text.startswith("foo… bar")


def test_b_verification_mismatch_no_wrong_deletion():
    class Liar(FakeBackend):
        """Second copy (the verification one) returns wrong text."""
        def copy(self):
            super().copy()
            self.n = getattr(self, "n", 0) + 1
            if self.n == 2:
                self.clipboard = "WRONG"
    fb = Liar()
    fb, logs, _, ok = run("keep [aa][bb] this text", mode="clipboard", fb=fb)
    assert ok
    assert fb.text == "keep aa this text"            # revision skipped, nothing deleted
    assert fb.cursor == len(fb.buf) and fb.errors == []
    assert any("verify" in l for l in logs) and any("skipped" in l for l in logs)


def test_b_nothing_copied_clipboard_mode_skips():
    fb, logs, _, ok = run("keep [aa][bb] this", mode="clipboard", fb=FakeBackend(clipboard_works=False))
    assert fb.text == "keep aa this" and any("skipped" in l for l in logs)


def test_b_crlf_clipboard_and_newlines():
    fb, logs, _, _ = run("line one\n[two\nthree][2 3] four\nend", mode="clipboard",
                         fb=FakeBackend(crlf_clipboard=True))
    assert fb.text == "line one\n2 3 four\nend"
    assert any("locate: clipboard" in l for l in logs)


def test_b_empty_first_and_empty_revised():
    fb, logs, _, _ = run("Hello[][, world]! and more text.", mode="clipboard")
    assert fb.text == "Hello, world! and more text."
    fb, logs, _, _ = run("Keep this[ - delete me][] and the rest", mode="clipboard")
    assert fb.text == "Keep this and the rest"
    fb, logs, _, _ = run("[][Start] then text", mode="clipboard")   # insertion at doc start
    assert fb.text == "Start then text"


def test_b_pre_existing_text_in_field():
    fb = FakeBackend(initial="existing stuff. ")
    fb, logs, _, _ = run("a [b][c] d", mode="clipboard", fb=fb)
    assert fb.text == "existing stuff. a c d"


# ---- fallback chain -------------------------------------------------------
def test_auto_chain_uia_available():
    fb = FakeBackend(locator_available=True)
    fb, logs, _, _ = run("I [realy][really] like it, ok.", fb=fb)
    assert fb.text == "I really like it, ok." and fb.cursor == len(fb.buf)
    assert any("locate: UIA ✓" in l for l in logs)
    assert not any("clipboard" in l for l in logs)
    assert fb.errors == []


def test_auto_chain_uia_unavailable_falls_to_clipboard():
    fb = FakeBackend(locator_available=False)
    fb, logs, _, _ = run("I [realy][really] like it, ok.", fb=fb)
    assert fb.text == "I really like it, ok."
    order = [l for l in logs if "REV 1/1" in l]
    assert "UIA" in order[0] and "✗" in order[0]
    assert "locate: clipboard ✓" in order[1]


def test_auto_chain_uia_and_clipboard_fail_falls_to_count():
    fb = FakeBackend(locator_available=False, clipboard_works=False)
    fb, logs, _, _ = run("I [realy][really] like it, ok.", fb=fb)
    assert fb.text == "I really like it, ok."
    joined = " | ".join(logs)
    assert joined.index("UIA") < joined.index("clipboard") < joined.index("counting") < joined.index("count (unverified)")


def test_uia_select_refused_restores_caret_and_falls_over():
    fb = FakeBackend()
    fb.locator = FakeLocator(fb, refuse_select=True)
    fb, logs, _, _ = run("I [realy][really] like it, ok.", fb=fb)
    assert fb.text == "I really like it, ok." and fb.cursor == len(fb.buf)
    assert any("select not verified" in l for l in logs)


def test_uia_handles_autocorrect_drift_and_duplicates():
    text = "[xx][yy] please go now, and then go again; yy yy end"
    fb = FakeBackend(autocorrect=extra_char_autocorrect, locator_available=True)
    fb, logs, _, _ = run(text, fb=fb)
    assert fb.text.startswith("yy please go! now")
    assert fb.cursor == len(fb.buf) and any("return: UIA" in l and "✓" in l for l in logs)


def test_uia_empty_first_uses_context():
    fb = FakeBackend(locator_available=True)
    fb, logs, _, _ = run("Hello[][, world]! more.", fb=fb)
    assert fb.text == "Hello, world! more."


def test_uia_ambiguous_fails_over():
    # two occurrences equally close to expected distance cannot be told apart
    fb = FakeBackend(locator_available=True, initial="")
    fb, logs, _, _ = run("[ab][X]ab", fb=fb)
    assert fb.text == "Xab"


# ---- callbacks ----------------------------------------------------------------
def test_on_char_and_on_log_called():
    fb, logs, chars, ok = run("ab [cd][ef] gh", typo=0.5, seed=3)
    assert ok and fb.text == "ab ef gh"
    assert "\b" in chars and "a" in chars
    assert any(l.startswith("typo '") for l in logs)
    assert any(l.startswith("REV 1/1") for l in logs)


def test_callbacks_exceptions_swallowed():
    def boom(_):
        raise RuntimeError("x")
    fb = FakeBackend()
    eng = TypingEngine(fb, Settings(typo_rate=0.3, seed=2), sleep=lambda s: None, on_log=boom, on_char=boom)
    assert eng.run("a [b][c] d e f g") is True
    assert fb.text == "a c d e f g"


@pytest.mark.parametrize("mode", ["auto", "clipboard", "count"])
@pytest.mark.parametrize("seed", range(5))
def test_all_modes_match_expected_without_drift(mode, seed):
    text = "Hi [aa][bb] there. [][X]More [cc][] stuff [dd][ee]!\nNew line [ff][gg] end."
    fb, logs, _, ok = run(text, mode=mode, seed=seed, typo=0.1, rev="random",
                          fb=FakeBackend(locator_available=(mode == "auto")))
    assert fb.text == parse(text).expected() and fb.cursor == len(fb.buf) and fb.errors == []
