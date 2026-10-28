import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
import pytest

from humantyper.backends import FakeBackend
from humantyper.engine import Settings, TypingEngine
from humantyper.timing import HumanTiming

KEYS = ("type_char", "press_enter", "press_tab", "backspace", "left", "right", "shift_left",
        "select_to_start", "ctrl_left", "ctrl_shift_left", "copy")


def logged(fb):
    log = []
    for name in KEYS:
        orig = getattr(fb, name)

        def wrap(*a, _o=orig, _n=name):
            log.append(_n)
            return _o(*a)
        setattr(fb, name, wrap)
    return log


def run(text, fb, mode="clipboard", seed=1):
    logs = []
    eng = TypingEngine(fb, Settings(typo_rate=0, revise_mode="end", seed=seed, locate_mode=mode),
                       sleep=lambda s: None, on_log=logs.append)
    assert eng.run(text)
    return logs


def squash(keys):
    out = []
    for k in keys:
        if out and out[-1][0] == k:
            out[-1][1] += 1
        else:
            out.append([k, 1])
    return out


TEXT = "Hello there, my friend. [realy][really] nice to see you again today, ok."


def test_b_key_sequence_no_round_trip():
    fb = FakeBackend()
    log = logged(fb)
    logs = run(TEXT, fb)
    assert fb.text == "Hello there, my friend. really nice to see you again today, ok."
    assert fb.cursor == len(fb.buf) and fb.errors == []
    nav = squash(log[log.index("ctrl_shift_left"):])
    names = [k for k, _ in nav]
    # selection back, copy, ONE Left collapse, short Right hop, (first-run verify), backspaces, revised, Right return
    assert names[:3] == ["ctrl_shift_left", "copy", "left"]
    assert names[3] == "right"
    assert "select_to_start" not in log
    assert log.count("left") == 1
    d = len(" nice to see you again today, ok.")
    assert nav[-1] == ["right", d]
    assert ["backspace", 5] in nav
    hop = nav[3][1]
    assert hop < 25                                   # short hop, not a walk over the text


def test_b_no_home_when_phrase_is_near_and_no_right_d_left_d():
    fb = FakeBackend(initial="x" * 500 + " ")          # long pre-existing document
    log = logged(fb)
    run("a b c d [bad][good] e f g", fb)
    assert "select_to_start" not in log
    assert log.count("left") == 1                     # old method walked Left x d


def test_b_verify_only_once_per_run():
    fb = FakeBackend()
    log = logged(fb)
    run("one [aa][bb] two [cc][dd] tail [pq][rs] end", fb)
    assert fb.text == "one bb two dd tail rs end"
    assert log.count("shift_left") == 2               # only first revision verified (no duplicates)
    assert log.count("copy") == 3 + 1


def test_b_drift_beyond_margin_extends_selection():
    def ac(fb):
        if fb.text.endswith("go "):
            fb.buf.extend(list("<<autocomplete suggestion inserted here>> "))
            fb.cursor = len(fb.buf)
    text = "[xx][yy] please go and more words after it, fine."
    fb = FakeBackend(autocorrect=ac)
    log = logged(fb)
    run(text, fb)
    assert fb.text.startswith("yy please go <<autocomplete")
    assert fb.cursor == len(fb.buf)
    assert log.count("copy") >= 3                     # >1 selection attempt (+verify)
    first_grow = log.index("ctrl_shift_left")
    assert log.count("ctrl_shift_left") > 4


def test_b_huge_drift_falls_back_to_home_and_still_correct():
    def ac(fb):
        if fb.text.endswith("go "):
            fb.buf.extend(list("Q" * 300))
            fb.cursor = len(fb.buf)
    fb = FakeBackend(autocorrect=ac)
    log = logged(fb)
    run("[xx][yy] please go on", fb)
    assert fb.text.startswith("yy please go ") and fb.errors == []
    assert fb.cursor == len(fb.buf)


@pytest.mark.parametrize("style", ["win", "chrome", "word"])
@pytest.mark.parametrize("seed", range(4))
def test_b_word_jump_variants(style, seed):
    text = ("It's (really) late, isn't it? [teh quick][the quick] brown fox -- jumps; over... "
            "[lazy][sleepy] dogs! Next: [a.b][c.d] done.\nNew line [ff][gg] end.")
    fb = FakeBackend(word_style=style)
    run(text, fb, seed=seed)
    exp = ("It's (really) late, isn't it? the quick brown fox -- jumps; over... "
           "sleepy dogs! Next: c.d done.\nNew line gg end.")
    assert fb.text == exp and fb.cursor == len(fb.buf) and fb.errors == []


def test_b_word_jump_unsupported_falls_back_to_home():
    fb = FakeBackend(word_jump=False)
    log = logged(fb)
    run("keep [aa][bb] this text", fb)
    assert fb.text == "keep bb this text" and "select_to_start" in log


def test_auto_falls_back_to_counting_when_everything_unsupported():
    fb = FakeBackend(word_jump=False, clipboard_works=False, locator_available=False)
    run("keep [aa][bb] this text", fb, mode="auto")
    assert fb.text == "keep bb this text" and fb.cursor == len(fb.buf)


def test_b_clipboard_restored_even_on_failure():
    fb = FakeBackend(clipboard_initial="mine", clipboard_works=False)
    run("keep [aa][bb] x", fb)
    assert fb.clipboard == "mine" and fb.text == "keep aa x" and fb.cursor == len(fb.buf)


def test_b_verify_mismatch_never_deletes_and_restores_caret():
    class Liar(FakeBackend):
        n = 0

        def copy(self):
            super().copy()
            Liar.n += 1
            if Liar.n == 2:
                self.clipboard = "WRONG"
    Liar.n = 0
    fb = Liar(clipboard_initial="keepme")
    logs = run("keep [aa][bb] this text", fb)
    assert fb.text == "keep aa this text" and fb.cursor == len(fb.buf) and fb.errors == []
    assert fb.clipboard == "keepme" and any("verify" in l for l in logs)


def test_clipboard_mode_skips_instead_of_guessing_when_not_found():
    # selection can never contain the phrase: field replaces its text -> skipped, nothing deleted
    class Weird(FakeBackend):
        def copy(self):
            super().copy()
            self.clipboard = "totally different"
    fb = Weird()
    logs = run("abc [aa][bb] def", fb)
    assert fb.text == "abc aa def" and fb.cursor == len(fb.buf)


def test_arrow_timing_is_fast():
    t = HumanTiming(60, random.Random(3))
    xs = [t.arrow_delay() for _ in range(2000)]
    assert min(xs) >= 0.025 and sum(xs) / len(xs) < 0.05 and max(xs) < 0.11


def test_return_trip_uses_only_right_keys():
    fb = FakeBackend()
    log = logged(fb)
    run("pre [aa][bb] some trailing text here", fb)
    tail = log[len(log) - len(" some trailing text here"):]
    assert set(tail) == {"right"}
