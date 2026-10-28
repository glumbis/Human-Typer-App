import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humantyper.parser import parse


def p(s):
    return parse(s)


def test_plain_text():
    pl = p("hello world")
    assert pl.text == "hello world" and pl.revisions == []


def test_single_revision():
    pl = p("I [realy][really] like it")
    assert pl.text == "I realy like it"
    r = pl.revisions[0]
    assert (r.start, r.first, r.revised, r.end) == (2, "realy", "really", 7)
    assert pl.expected() == "I really like it"


def test_lone_brackets_literal():
    assert p("a [x] b").text == "a [x] b"
    assert p("a [x] b").revisions == []
    assert p("a [x] [y]").text == "a [x] [y]"      # space between: not a revision
    assert p("[unclosed").text == "[unclosed"
    assert p("stray ] bracket").text == "stray ] bracket"


def test_escapes():
    pl = p(r"\[not\] a [rev\]x][done\[y]")
    assert pl.text == "[not] a rev]x"
    assert pl.revisions[0].first == "rev]x"
    assert pl.revisions[0].revised == "done[y"
    assert pl.expected() == "[not] a done[y"


def test_escaped_pair_is_literal():
    pl = p(r"\[a\]\[b\]")
    assert pl.text == "[a][b]" and pl.revisions == []


def test_empty_first_insert():
    pl = p("ab[][XY]cd")
    r = pl.revisions[0]
    assert pl.text == "abcd" and r.start == 2 and r.first == "" and r.revised == "XY"
    assert pl.expected() == "abXYcd"


def test_empty_revised_delete():
    pl = p("ab[delete me][]cd")
    assert pl.text == "abdelete mecd"
    assert pl.revisions[0].revised == ""
    assert pl.expected() == "abcd"


def test_multiple_and_adjacent():
    pl = p("[a][b][c][d] [e][f][][g][h][i]")
    assert pl.text == "ac eh"
    assert pl.expected() == "bd fgi"
    assert [r.start for r in pl.revisions] == [0, 1, 3, 4, 4]


def test_newlines():
    pl = p("x[one\ntwo][1\n2]y\nz")
    assert pl.text == "xone\ntwoy\nz"
    assert pl.expected() == "x1\n2y\nz"


def test_crlf_normalised():
    assert p("a\r\nb").text == "a\nb"


def test_no_nesting_first_close_wins():
    pl = p("[a[b][c]")
    assert pl.revisions[0].first == "a[b" and pl.revisions[0].revised == "c"


def test_norwegian_chars():
    pl = p("Jeg [liker][elsker] blåbærsyltetøy")
    assert pl.expected() == "Jeg elsker blåbærsyltetøy"
