from humantyper.ui.stats import Stats, fmt_time
from humantyper.ui import art, theme as T


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_fmt_time():
    assert fmt_time(None) == "--:--"
    assert fmt_time(75) == "01:15"
    assert fmt_time(3725) == "1:02:05"


def test_wpm_typos_progress_eta():
    clk = Clock()
    s = Stats(clock=clk)
    s.begin(100)
    for i in range(50):                  # 50 chars over 5 s => 120 wpm
        clk.t += 0.1
        s.on_char("a")
    s.on_char("\b")
    assert s.typos == 1 and s.typed == 50 and s.net_chars() == 49
    assert 100 < s.wpm() < 140
    s.on_progress(0.5)
    assert abs(s.eta() - s.elapsed()) < 1.0
    clk.t += 20                          # window decays when idle
    assert s.wpm() == 0.0


def test_pause_freezes_clock():
    clk = Clock()
    s = Stats(clock=clk)
    s.begin(10)
    clk.t += 5
    s.pause()
    clk.t += 30
    assert abs(s.elapsed() - 5) < 1e-6
    s.resume()
    clk.t += 2
    assert abs(s.elapsed() - 7) < 1e-6


def test_revision_parsing_and_status_fallback():
    s = Stats(clock=Clock())
    s.begin(10)
    s.on_log("REV 2/3 // locate: UIA OK")
    assert s.rev_total == 3 and s.rev_done == 1
    s.on_log("something unrelated")
    assert s.revisions_text() == "1/3"
    s2 = Stats(clock=Clock())
    s2.begin(10)
    s2.on_status("revising")
    s2.on_status("typing")                # log format changed: fall back to transitions
    assert s2.rev_done == 1 and s2.revisions_text() == "1/?"


def test_banner_and_face_shapes():
    b = art.Banner()
    rows = b.cells(3, 0.1)
    assert len(rows) == b.rows and all(len(r) == b.cols for r in rows)
    colors = art.Banner.tag_colors()
    assert all(t is None or t in colors for r in rows for _, t in r)
    f = art.Face()
    for p in (0.0, 0.5, 1.0):
        g = f.cells(p, 4)
        assert len(g) == f.h and all(len(r) == f.w for r in g)
        fc = art.Face.tag_colors()
        assert all(t is None or t in fc for r in g for _, t in r)
    # dissolving removes face characters
    dense = lambda g: sum(1 for r in g for c, _ in r if c in "$@8%B&WM#")
    assert dense(f.cells(0.9, 0)) < dense(f.cells(0.0, 0))


def test_theme_helpers():
    assert T.mix("#000000", "#ffffff", 0.5) == "#7f7f7f"
    assert T.gradient(0.0) == "#9b5cff"
