from humantyper.ui import background as B


def test_quantize_monotonic_and_bounded():
    prev = 0
    for i in range(0, 200):
        lv = B.quantize(i / 100.0)
        assert 0 <= lv <= B.NLEV
        assert lv >= prev
        prev = lv
    assert B.quantize(0.0) == 0
    assert B.quantize(5.0) == B.NLEV


def test_palette_and_ramp():
    assert B.RAMP[0] == " " and len(set(B.RAMP)) == len(B.RAMP)
    assert len(B.palette()) == B.K
    assert all(c.startswith("#") and len(c) == 7 for c in B.palette())
