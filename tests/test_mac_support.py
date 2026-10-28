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
