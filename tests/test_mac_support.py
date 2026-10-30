"""macOS logic tested on any OS with fakes (no pyobjc, no pynput hardware)."""
import types

import pytest

from humantyper import plat
from humantyper import hotkeys as hk
from humantyper.backends import PynputBackend
from humantyper.clipboard_mac import NSPasteboardBackend, SubprocessBackend
from humantyper.locator_mac import AxLocator, has_astral


# ---------------- keystroke mapping ----------------
class _FakeKey:
    shift, ctrl, cmd, alt, home, up, left, enter, tab, backspace, right = (
        "shift", "ctrl", "cmd", "alt", "home", "up", "left", "enter", "tab", "backspace", "right")


class _FakeKb:
    def __init__(self):
        self.log = []

    def press(self, k):
        self.log.append(("down", k))

    def release(self, k):
        self.log.append(("up", k))

    def type(self, ch):
        self.log.append(("type", ch))


def _backend(monkeypatch, mac):
    monkeypatch.setattr(plat, "IS_MAC", mac)
    monkeypatch.setattr(plat, "IS_WIN", not mac)
    monkeypatch.setattr(plat, "CMD_KEY", "cmd" if mac else "ctrl")
    monkeypatch.setattr(plat, "WORD_KEY", "alt" if mac else "ctrl")
    monkeypatch.setattr("time.sleep", lambda s: None)
    b = PynputBackend.__new__(PynputBackend)
    b._kb, b._Key = _FakeKb(), _FakeKey
    return b


def _downs(b):
    return [k for a, k in b._kb.log if a == "down"]


@pytest.mark.parametrize("mac,copy,word,wordsel,start", [
    (True, ["cmd", "c"], ["alt", "left"], ["alt", "shift", "left"], ["cmd", "shift", "up"]),
    (False, ["ctrl", "c"], ["ctrl", "left"], ["ctrl", "shift", "left"], ["ctrl", "shift", "home"]),
])
def test_shortcuts(monkeypatch, mac, copy, word, wordsel, start):
    for meth, want in (("copy", copy), ("ctrl_left", word), ("ctrl_shift_left", wordsel),
                       ("select_to_start", start)):
        b = _backend(monkeypatch, mac)
        getattr(b, meth)()
        assert _downs(b) == want, meth
        ups = [k for a, k in b._kb.log if a == "up"]
        assert sorted(ups) == sorted(want)          # everything released


# ---------------- clipboard ----------------
class FakeItem:
    def __init__(self, data=None):
        self.data = dict(data or {})

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def types(self):
        return list(self.data)

    def dataForType_(self, t):
        return self.data.get(t)

    def setData_forType_(self, d, t):
        self.data[t] = d


class FakeNSData:
    @staticmethod
    def dataWithBytes_length_(b, n):
        return bytes(b[:n])


class FakePB:
    def __init__(self):
        self.count = 1
        self.items = []

    def changeCount(self):
        return self.count

    def clearContents(self):
        self.items, self.count = [], self.count + 1

    def stringForType_(self, t):
        for it in self.items:
            if t in it.data:
                return it.data[t].decode()
        return None

    def setString_forType_(self, s, t):
        self.items.append(FakeItem({t: s.encode()}))
        return True

    def pasteboardItems(self):
        return list(self.items)

    def writeObjects_(self, objs):
        self.items.extend(objs)
        return True


def _mac_cb():
    pb = FakePB()
    return pb, NSPasteboardBackend(pb, "public.utf8-plain-text", FakeItem, FakeNSData)


def test_mac_clipboard_text_and_seq():
    pb, cb = _mac_cb()
    assert cb.get_text() is None
    s0 = cb.sequence_number()
    assert cb.set_text("héllo")
    assert cb.sequence_number() != s0
    assert cb.get_text() == "héllo"


def test_mac_clipboard_snapshot_restore_all_formats():
    pb, cb = _mac_cb()
    pb.items = [FakeItem({"public.utf8-plain-text": b"abc", "public.html": b"<b>abc</b>"}),
                FakeItem({"public.file-url": b"file:///x"})]
    snap = cb.snapshot()
    assert snap == [[("public.utf8-plain-text", b"abc"), ("public.html", b"<b>abc</b>")],
                    [("public.file-url", b"file:///x")]]
    cb.set_text("temp")
    assert cb.restore(snap)
    assert cb.get_text() == "abc"
    assert pb.items[1].data == {"public.file-url": b"file:///x"}
    assert pb.items[0].data["public.html"] == b"<b>abc</b>"
    assert not cb.restore(None)


def test_mac_clipboard_restore_text_fallback():
    pb, cb = _mac_cb()
    cb.item_cls = None                                   # item API unavailable
    assert cb.restore([[("public.utf8-plain-text", b"hi")]])
    assert cb.get_text() == "hi"


def test_mac_clipboard_empty_snapshot_clears():
    pb, cb = _mac_cb()
    cb.set_text("x")
    assert cb.restore([])
    assert cb.get_text() is None


def test_subprocess_fallback():
    store = {"t": b""}

    def run(cmd, input=None, capture_output=False, timeout=None, check=False):
        if cmd[0] == "pbcopy":
            store["t"] = input
        return types.SimpleNamespace(stdout=store["t"])
    c = SubprocessBackend(run)
    s0 = c.sequence_number()
    assert c.set_text("zz")
    assert c.get_text() == "zz" and c.sequence_number() != s0
    snap = c.snapshot()
    c.set_text("other")
    assert c.restore(snap) and c.get_text() == "zz"


# ---------------- AX locator ----------------
class FakeAx:
    def __init__(self, text="", caret=0, trusted=True, supported=True, set_works=True, focused=True):
        self.text, self.caret, self.len = text, caret, 0
        self._trusted, self.supported, self.set_works, self.focused = trusted, supported, set_works, focused
        self.sets = []

    def trusted(self):
        return self._trusted

    def focused_element(self):
        return object() if self.focused else None

    def get_text(self, el):
        return self.text if self.supported else None

    def get_caret(self, el):
        return (self.caret, self.len) if self.supported else None

    def set_caret(self, el, loc):
        self.sets.append(loc)
        if self.set_works:
            self.caret, self.len = loc, 0
        return True


def test_ax_snapshot_and_select():
    ax = FakeAx("hello world", 11)
    loc = AxLocator(ax)
    assert loc.name == "AX"
    assert loc.snapshot() == ("hello world", 11)
    assert loc.select(5, "hello") and ax.caret == 5
    assert not loc.select(5, "wrong")                      # verify mismatch
    assert not loc.select(99, "x") and ax.sets == [5, 5]   # out of range never set


def test_ax_select_not_applied_fails_over():
    ax = FakeAx("hello world", 11, set_works=False)
    assert not AxLocator(ax).select(5, "hello")


@pytest.mark.parametrize("kw", [dict(trusted=False), dict(supported=False), dict(focused=False)])
def test_ax_declines(kw):
    loc = AxLocator(FakeAx("abc", 1, **kw))
    assert loc.snapshot() is None and not loc.select(1, "a") and loc.last_error


def test_ax_declines_astral():
    assert has_astral("a\U0001F600") and not has_astral("åäö€")
    assert AxLocator(FakeAx("hi \U0001F600", 3)).snapshot() is None
    assert AxLocator(FakeAx("åäö€ ok", 4)).snapshot() == ("åäö€ ok", 4)


def test_ax_timeout_disables():
    class Hang(FakeAx):
        def trusted(self):
            import time
            time.sleep(0.5)
            return True
    loc = AxLocator(Hang("a", 0))
    loc.TIMEOUT = 0.05
    assert loc.snapshot() is None and loc.snapshot() is None
    assert "timed out" in loc.last_error or "disabled" in loc.last_error


# ---------------- hotkeys (pure logic) ----------------
def _mk():
    calls = []
    return calls, {hk.MAC_KC_F8: lambda: calls.append("f8"), hk.MAC_KC_F9: lambda: calls.append("f9")}


def test_mac_hotkeys_down_up_repeat():
    calls, cbs = _mk()
    held = set()
    h = lambda t, kc, rep=0, pid=0: hk.mac_handle(held, cbs, t, kc, rep, pid, 4242)
    assert h(hk.MAC_KEYDOWN, 100) is True and calls == ["f8"]
    assert h(hk.MAC_KEYDOWN, 100) is True and calls == ["f8"]            # held, no repeat
    assert h(hk.MAC_KEYDOWN, 100, rep=1) is True and calls == ["f8"]
    assert h(hk.MAC_KEYUP, 100) is True
    assert h(hk.MAC_KEYDOWN, 101) is True and calls == ["f8", "f9"]
    assert h(hk.MAC_KEYUP, 101) is True
    assert h(hk.MAC_KEYDOWN, 100) is True and calls == ["f8", "f9", "f8"]


def test_mac_hotkeys_pass_through_and_own_events():
    calls, cbs = _mk()
    held = set()
    assert hk.mac_handle(held, cbs, hk.MAC_KEYDOWN, 0, 0, 0, 1) is False   # 'a'
    assert hk.mac_handle(held, cbs, hk.MAC_KEYDOWN, 100, 0, 4242, 4242) is False  # injected by us
    assert hk.mac_handle(held, cbs, 12, 100, 0, 0, 1) is False             # flagsChanged
    assert calls == []


def test_mac_hotkeys_callback_error_swallowed():
    def boom():
        raise ValueError
    assert hk.mac_handle(set(), {100: boom}, hk.MAC_KEYDOWN, 100) is True


def test_windows_handle_unchanged():
    calls = []
    g = hk.GlobalHotkeys(lambda: calls.append(8), lambda: calls.append(9))
    assert g.handle(0x100, hk.VK_F8, 0) is True and calls == [8]
    assert g.handle(0x100, hk.VK_F8, hk.LLKHF_INJECTED) is False
    assert g.ok is False and g.error == ""


# ---------------- macOS event tap (fake Quartz) ----------------
import threading as _th


class FakeQuartz:
    kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault = 1, 0, 0
    kCFRunLoopCommonModes = "common"

    def __init__(self, tap_ok=True):
        self.tap_ok = tap_ok
        self.created = None
        self.enabled = []
        self.stopped = _th.Event()
        self.run_thread = None

    def CGEventTapCreate(self, where, place, opts, mask, cb, refcon):
        self.created = (where, place, opts, mask, cb)
        return "TAP" if self.tap_ok else None

    def CFMachPortCreateRunLoopSource(self, alloc, tap, order):
        return "SRC"

    def CFRunLoopGetCurrent(self):
        return "LOOP"

    def CFRunLoopAddSource(self, loop, src, mode):
        pass

    def CGEventTapEnable(self, tap, on):
        self.enabled.append(on)

    def CFRunLoopRun(self):
        self.run_thread = _th.current_thread()
        self.stopped.wait(5)

    def CFRunLoopStop(self, loop):
        self.stopped.set()


def test_mac_event_tap_runs_on_own_thread_and_filters():
    seen = []
    q = FakeQuartz()
    tap = hk.MacEventTap(lambda t, e: seen.append((t, e)) or e == "F8", quartz=q)
    tap.start()
    assert q.created[3] == hk.MAC_KEY_MASK == (1 << 10) | (1 << 11)
    assert q.created[2] == q.kCGEventTapOptionDefault      # active tap: may swallow events
    cb = q.created[4]
    assert cb(None, hk.MAC_KEYDOWN, "F8", None) is None     # swallowed
    assert cb(None, hk.MAC_KEYDOWN, "a", None) == "a"       # passed through
    assert cb(None, 0xFFFFFFFE, "x", None) == "x" and q.enabled[-1] is True   # re-armed after timeout
    tap.stop()
    assert q.stopped.is_set() and q.enabled[-1] is False
    assert q.run_thread is not _th.main_thread()


def test_mac_event_tap_handler_error_passes_event():
    q = FakeQuartz()
    tap = hk.MacEventTap(lambda t, e: 1 / 0, quartz=q)
    tap.start()
    assert q.created[4](None, hk.MAC_KEYDOWN, "ev", None) == "ev"
    tap.stop()


def test_mac_event_tap_without_permission_raises():
    q = FakeQuartz(tap_ok=False)
    tap = hk.MacEventTap(lambda t, e: False, quartz=q)
    with pytest.raises(RuntimeError, match="Input Monitoring"):
        tap.start()
    tap.stop()                                               # harmless


def test_global_hotkeys_mac_uses_event_tap_not_pynput(monkeypatch):
    """pynput's mac Listener reads the keyboard layout (TSM) on its own thread, which kills the
    process on macOS 14+; GlobalHotkeys must use MacEventTap instead."""
    monkeypatch.setattr(hk.plat, "IS_MAC", True)
    started = []
    monkeypatch.setattr(hk.MacEventTap, "start", lambda self, timeout=3.0: started.append(self))
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "pynput", None)        # any pynput import would fail
    g = hk.GlobalHotkeys(lambda: None, lambda: None)
    g.start()
    assert g.ok and isinstance(started[0], hk.MacEventTap)


# ---------------- event tap fallback / retry ----------------
class FakeQuartzModes(FakeQuartz):
    kCGEventTapOptionListenOnly = 1

    def __init__(self, allowed=("active", "listen")):
        super().__init__()
        self.allowed = set(allowed)
        self.tried = []

    def CGEventTapCreate(self, where, place, opts, mask, cb, refcon):
        mode = "active" if opts == self.kCGEventTapOptionDefault else "listen"
        self.tried.append(mode)
        self.created = (where, place, opts, mask, cb)
        return "TAP" if mode in self.allowed else None


@pytest.mark.parametrize("allowed,mode,tried", [
    (("active", "listen"), "active", ["active"]),
    (("listen",), "listen", ["active", "listen"]),     # only Input Monitoring: listen-only tap
])
def test_mac_event_tap_falls_back_to_listen_only(allowed, mode, tried):
    q = FakeQuartzModes(allowed)
    tap = hk.MacEventTap(lambda t, e: True, quartz=q)
    tap.start()
    assert tap.mode == mode and q.tried == tried
    tap.stop()


def test_mac_event_tap_no_permission_at_all():
    q = FakeQuartzModes(())
    tap = hk.MacEventTap(lambda t, e: True, quartz=q)
    with pytest.raises(RuntimeError, match="Input Monitoring"):
        tap.start()
    assert q.tried == ["active", "listen"] and tap.mode is None


class _FakeTap:
    """Stands in for MacEventTap; FakeTap.allowed decides what start() can create."""
    allowed = ()
    made = []

    def __init__(self, handler, quartz=None):
        self.mode, self.stopped = None, False
        _FakeTap.made.append(self)

    def start(self, timeout=3.0):
        if not _FakeTap.allowed:
            raise RuntimeError("no permission")
        self.mode = _FakeTap.allowed[0]

    def stop(self):
        self.stopped = True


@pytest.fixture
def fake_tap(monkeypatch):
    monkeypatch.setattr(hk.plat, "IS_MAC", True)
    monkeypatch.setattr(hk, "MacEventTap", _FakeTap)
    _FakeTap.allowed, _FakeTap.made = (), []
    return _FakeTap


def test_hotkeys_retry_until_permission_granted(fake_tap):
    g = hk.GlobalHotkeys(lambda: None, lambda: None)
    with pytest.raises(RuntimeError):
        g.start()
    assert not g.ok and g.mode is None
    assert g.retry() is False and g.mode is None           # still no permission
    fake_tap.allowed = ("active",)                          # user grants it: no restart needed
    assert g.retry() is True and g.ok and g.mode == "active"
    n = len(fake_tap.made)
    assert g.retry() is True and len(fake_tap.made) == n    # running: nothing recreated


def test_hotkeys_upgrade_listen_to_active(fake_tap):
    fake_tap.allowed = ("listen",)
    g = hk.GlobalHotkeys(lambda: None, lambda: None)
    g.start()
    first = g._listener
    assert g.mode == "listen"
    assert g.retry(upgrade=True) and g._listener is first and g.mode == "listen"   # no better yet
    assert fake_tap.made[-1].stopped and not first.stopped
    fake_tap.allowed = ()
    assert g.retry(upgrade=True) and g._listener is first and g.ok                 # failure keeps it
    fake_tap.allowed = ("active",)
    assert g.retry(upgrade=True) and g.mode == "active" and first.stopped


def test_hotkeys_stop_marks_off(fake_tap):
    fake_tap.allowed = ("active",)
    g = hk.GlobalHotkeys(lambda: None, lambda: None)
    g.start()
    g.stop()
    assert not g.ok and g.mode is None


def test_windows_mode_is_hook():
    g = hk.GlobalHotkeys(lambda: None, lambda: None)
    g.ok, g._listener = True, object()
    assert g.mode == "hook"


def test_mac_fn_f8_flags_do_not_matter():
    """fn+F8 arrives as keycode 100 with the SecondaryFn flag; mac_handle only looks at the keycode."""
    calls = []
    held = set()
    cbs = {hk.MAC_KC_F8: lambda: calls.append(8)}
    assert hk.mac_handle(held, cbs, hk.MAC_KEYDOWN, 100, 0, 999, 1) is True and calls == [8]


# ---------------- Quartz keystroke backend ----------------
from humantyper import backends as B


class FakeQ:
    kCGEventSourceStateHIDSystemState, kCGHIDEventTap = 1, 0

    def __init__(self):
        self.posted = []

    def CGEventSourceCreate(self, state):
        return "SRC"

    def CGEventCreateKeyboardEvent(self, src, kc, down):
        return {"kc": kc, "down": down, "flags": None, "text": None, "src": src}

    def CGEventKeyboardSetUnicodeString(self, ev, n, text):
        ev["text"], ev["n"] = text, n

    def CGEventSetFlags(self, ev, flags):
        ev["flags"] = flags

    def CGEventPost(self, tap, ev):
        self.posted.append(ev)


def _qb():
    q = FakeQ()
    return B.QuartzBackend(quartz=q, sleep=lambda s: None), q


def _plain(flags):
    return flags & ~B.MAC_FLAG_NONCOALESCED


def test_quartz_type_char_unicode():
    b, q = _qb()
    for ch in "aÅ€😀":
        b.type_char(ch)
    downs = [e for e in q.posted if e["down"]]
    assert [e["text"] for e in downs] == list("aÅ€😀")
    assert [e["n"] for e in downs] == [1, 1, 1, 2]           # UTF-16 length (emoji = surrogate pair)
    assert all(_plain(e["flags"]) == 0 for e in q.posted)    # no modifiers leak into text
    assert len(q.posted) == 8 and all(e["src"] == "SRC" for e in q.posted)


@pytest.mark.parametrize("meth,kc", [("press_enter", 36), ("press_tab", 48), ("backspace", 51),
                                     ("left", 123), ("right", 124)])
def test_quartz_special_keys(meth, kc):
    b, q = _qb()
    getattr(b, meth)()
    assert [(e["kc"], e["down"], _plain(e["flags"])) for e in q.posted] == [(kc, True, 0), (kc, False, 0)]


def test_quartz_newline_and_tab_via_type_char():
    b, q = _qb()
    b.type_char("\n")
    b.type_char("\t")
    assert [e["kc"] for e in q.posted] == [36, 36, 48, 48] and all(e["text"] is None for e in q.posted)


SHIFT, ALT, CMD = B.MAC_FLAG["shift"], B.MAC_FLAG["alt"], B.MAC_FLAG["cmd"]


@pytest.mark.parametrize("meth,mods,kc,flags", [
    ("shift_left", [56], 123, SHIFT),
    ("ctrl_left", [58], 123, ALT),                     # Option+Left = word jump on a Mac
    ("ctrl_shift_left", [58, 56], 123, ALT | SHIFT),
    ("select_to_start", [55, 56], 126, CMD | SHIFT),   # Cmd+Shift+Up
    ("copy", [55], 8, CMD),                            # Cmd+C
])
def test_quartz_combos_set_flags_and_release(meth, mods, kc, flags):
    b, q = _qb()
    getattr(b, meth)()
    ev = [(e["kc"], e["down"], _plain(e["flags"])) for e in q.posted]
    n = len(mods)
    assert [k for k, d, _ in ev[:n]] == mods and all(d for _, d, _ in ev[:n])     # modifiers down
    assert ev[n:n + 2] == [(kc, True, flags), (kc, False, flags)]               # key with all flags
    ups = ev[n + 2:]
    assert [k for k, _, _ in ups] == list(reversed(mods)) and not any(d for _, d, _ in ups)
    assert ups[-1][2] == 0                                                      # nothing sticks


def test_quartz_combo_releases_modifiers_on_error():
    b, q = _qb()
    orig = b._key

    def boom(*a, **k):
        raise RuntimeError("post failed")
    b._key = boom
    with pytest.raises(RuntimeError):
        b.copy()
    assert q.posted[-1]["kc"] == 55 and q.posted[-1]["down"] is False and _plain(q.posted[-1]["flags"]) == 0
    b._key = orig


def test_make_backend_per_os(monkeypatch):
    made = []
    monkeypatch.setattr(B, "QuartzBackend", lambda: made.append("q") or "Q")
    monkeypatch.setattr(B, "PynputBackend", lambda: made.append("p") or "P")
    monkeypatch.setattr(B.plat, "IS_MAC", True)
    assert B.make_backend() == "Q"
    monkeypatch.setattr(B.plat, "IS_MAC", False)
    assert B.make_backend() == "P"


def test_ax_range_tuple_accepts_pyobjc_tuple_and_struct():
    from humantyper.locator_mac import range_tuple
    assert range_tuple((53, 0)) == (53, 0)                  # what pyobjc really returns (CI probe)
    assert range_tuple(types.SimpleNamespace(location=7, length=2)) == (7, 2)
