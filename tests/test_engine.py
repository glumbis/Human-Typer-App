import os, sys, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from humantyper.backends import FakeBackend
from humantyper.engine import Settings, TypingEngine, NEIGHBORS
from humantyper.hotkeys import GlobalHotkeys, VK_F8, VK_F9
from humantyper.parser import parse

LONG = ("Hei! Jeg heter Åse, og jeg liker [blåbær][bringebær] og [the cat][a dog].\n"
        "Second line: the quick brown fox jumps over the lazy dog; 12345 times!\tTabbed.\n"
        "[][Inserted later. ]Then more text follows here, quite a lot of it, so that random "
        "triggers have room to fire: " + "lorem ipsum dolor sit amet, consectetur. " * 12 +
        "[delete me][]Ending [x] literal and \\[escaped\\] [old\nline][new\nline] end.")

CASES = [
    "plain text without any revisions",
    "I [realy][really] like it",
    "[][start]",
    "x[gone][]",
    "[a][b][c][d][e][f]",
    "[ab][cd][][ins][del][]tail",
    "[first\nsecond][one]\nrest of it\n",
    "",
    LONG,
]


def run(text, seed, mode, typo, wpm=80):
    fb = FakeBackend()
    eng = TypingEngine(fb, Settings(wpm=wpm, typo_rate=typo, revise_mode=mode, seed=seed),
                       sleep=lambda s: None)
    assert eng.run(text) is True
    return fb


@pytest.mark.parametrize("text", CASES)
@pytest.mark.parametrize("mode", ["random", "end"])
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("typo", [0.0, 0.10, 0.5])
def test_final_text(text, mode, seed, typo):
    fb = run(text, seed, mode, typo)
    expected = parse(text).expected()
    assert fb.text == expected
    assert fb.cursor == len(fb.buf)
    assert fb.errors == []


def test_revision_key_sequence_exact():
    fb = FakeBackend()
    log = []
    for name in ("type_char", "press_enter", "press_tab", "backspace", "left", "right"):
        orig = getattr(fb, name)
        def wrap(*a, _o=orig, _n=name):
            log.append((_n, a[0] if a else None))
            return _o(*a)
        setattr(fb, name, wrap)
    sleeps = []
    eng = TypingEngine(fb, Settings(typo_rate=0, revise_mode="end", seed=1), sleep=sleeps.append)
    eng.run("ab[cd][XYZ]ef\ng")
    assert fb.text == "abXYZef\ng"
    keys = [k for k, _ in log]
    # first pass types a b c d e f \n g, then Left x4 (e f \n g), Backspace x2, type XYZ, Right x4
    first_pass = keys[:8]
    assert first_pass == ["type_char"] * 6 + ["press_enter", "type_char"]
    assert keys[8:12] == ["left"] * 4
    assert keys[12:14] == ["backspace"] * 2
    assert keys[14:17] == ["type_char"] * 3
    assert keys[17:] == ["right"] * 4
    # thinking pause was ~3.5-4.5 s in total (sleep sliced)
    assert sum(sleeps) > 3.5


def test_random_mode_revises_before_end():
    text = "[aaa][bbb]" + "word " * 200 + "END"
    fb = FakeBackend()
    snapshots = []
    eng = TypingEngine(fb, Settings(typo_rate=0, revise_mode="random", seed=7), sleep=lambda s: None,
                       on_status=lambda k: snapshots.append((k, len(fb.buf))))
    eng.run(text)
    revising_at = [n for k, n in snapshots if k == "revising"]
    assert revising_at and revising_at[0] < len(parse(text).text) - 50
    assert fb.text.startswith("bbbword word")


def test_typos_actually_happen_and_get_fixed():
    fb = FakeBackend()
    eng = TypingEngine(fb, Settings(typo_rate=0.3, seed=3), sleep=lambda s: None)
    txt = "the quick brown fox jumps over the lazy dog " * 5
    eng.run(txt)
    assert fb.text == txt
    assert fb.keys > len(txt) * 1.2      # extra keystrokes = typos + backspaces


def test_no_typo_on_newline_and_case_kept():
    for seed in range(20):
        fb = FakeBackend()
        calls = []
        orig = fb.press_enter
        fb.press_enter = lambda: (calls.append(1), orig())
        txt = "A\nB\nC\n"
        TypingEngine(fb, Settings(typo_rate=1.0, seed=seed), sleep=lambda s: None).run(txt)
        assert fb.text == txt
        assert len(calls) == 3        # Enter pressed exactly once per newline


def test_neighbors_include_norwegian():
    assert "å" in NEIGHBORS["p"] and "ø" in NEIGHBORS["l"] and "æ" in NEIGHBORS["ø"]


def test_stop():
    fb = FakeBackend()
    holder = {}
    n = {"c": 0}
    def sl(s):
        n["c"] += 1
        if n["c"] == 50:
            holder["e"].stop()
    eng = TypingEngine(fb, Settings(typo_rate=0), sleep=sl)
    holder["e"] = eng
    assert eng.run("x" * 500) is False
    assert 0 < len(fb.buf) < 500


def test_pause_resume_thread():
    fb = FakeBackend()
    done = threading.Event()
    result = []
    state = {"paused": False}
    def prog(f):
        if f > 0.3 and not state["paused"]:
            state["paused"] = True
            eng.pause()
            threading.Timer(0.2, eng.resume).start()
    eng = TypingEngine(fb, Settings(typo_rate=0), sleep=lambda s: None, on_progress=prog,
                       on_finish=lambda r: (result.append(r), done.set()))
    eng.start("hello world " * 20)
    assert done.wait(10)
    assert result == ["done"] and fb.text == "hello world " * 20


def test_hotkey_logic():
    hits = []
    hk = GlobalHotkeys(lambda: hits.append("f8"), lambda: hits.append("f9"))
    assert hk.handle(0x100, VK_F8, 0) is True
    assert hk.handle(0x100, VK_F8, 0) is True        # auto-repeat suppressed, no 2nd callback
    assert hk.handle(0x101, VK_F8, 0) is True
    assert hk.handle(0x100, VK_F9, 0) is True
    assert hits == ["f8", "f9"]
    assert hk.handle(0x100, VK_F8, 0x10) is False    # injected: ignored, not suppressed
    assert hk.handle(0x100, 0x1B, 0) is False        # Esc never touched
    assert hk.handle(0x100, 0x41, 0) is False
    assert hits == ["f8", "f9"]


def test_import_smoke_app(monkeypatch):
    import humantyper.app as app
    monkeypatch.setattr(app.config, "save", lambda *a, **k: None)
    monkeypatch.setattr(app.config, "load", lambda *a, **k: dict(app.config.DEFAULTS))
    monkeypatch.setattr(app.GlobalHotkeys, "start", lambda self: None)
    assert hasattr(app, "HumanTyperApp")
    ctk_ok = True
    try:
        w = app.HumanTyperApp()
    except Exception as e:      # no display available
        ctk_ok = False
        pytest.skip(f"no display: {e}")
    if ctk_ok:
        w.update()
        w._on_close()
