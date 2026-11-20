"""KEYBOARD GUARD (block while typing, track + erase while paused) and FOCUS GUARD."""
import os, sys, threading, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from humantyper import hotkeys as hk
from humantyper.backends import FakeBackend
from humantyper.engine import Erase, Guard, Settings, TypingEngine
from humantyper.hotkeys import (FocusGuard, GlobalHotkeys, KeyGuard, LLKHF_INJECTED, VK_F8, VK_F9,
                                G_BLOCK, G_OFF, G_TRACK)

DOWN, UP = 0x100, 0x101
SYSDOWN = 0x104
VK_A, VK_X, VK_V, VK_SHIFT, VK_LCTRL, VK_RALT, VK_LEFT = 0x41, 0x58, 0x56, 0xA0, 0xA2, 0xA5, 0x25


def tap(g, vk, flags=0):
    a = g.win_key(DOWN, vk, flags)
    b = g.win_key(UP, vk, flags)
    return a, b


def tracking(**kw):
    g = KeyGuard(True, **kw)
    g.engine_state("start")
    g.engine_state("paused")
    return g


# ---------------- block ----------------
def test_block_swallows_physical_keys_not_injected():
    g = KeyGuard(True)
    g.engine_state("start")
    assert g.state == G_BLOCK
    assert tap(g, VK_A) == (True, True)
    assert tap(g, VK_LEFT) == (True, True)
    assert g.win_key(SYSDOWN, 0x12, 0) is True               # Alt
    assert tap(g, VK_A, LLKHF_INJECTED) == (False, False)    # our own keystrokes
    g.engine_state("paused")
    assert tap(g, VK_A) == (False, False)
    g.engine_state("typing")
    assert tap(g, VK_A) == (True, True)
    g.engine_state("idle")
    assert g.state == G_OFF and tap(g, VK_A) == (False, False)


def test_disabled_guard_never_blocks():
    g = KeyGuard(False)
    g.engine_state("start")
    assert g.state == G_OFF and tap(g, VK_A) == (False, False)


def test_keys_held_before_block_release_normally():
    g = KeyGuard(True)
    assert g.win_key(DOWN, VK_SHIFT, 0) is False             # user holds Shift (and 'a') ...
    assert g.win_key(DOWN, VK_A, 0) is False
    g.engine_state("start")                                  # ... when typing starts
    assert g.win_key(DOWN, VK_A, 0) is True                  # auto-repeat is blocked
    assert g.win_key(UP, VK_A, 0) is False                   # but the release gets through
    assert g.win_key(UP, VK_SHIFT, 0) is False
    assert g.win_key(DOWN, VK_SHIFT, 0) is True              # pressed again while blocking
    assert g.win_key(UP, VK_SHIFT, 0) is True                # its whole press stays swallowed


def test_global_hotkeys_f8_f9_still_work_while_blocking():
    hits = []
    g = KeyGuard(True)
    h = GlobalHotkeys(lambda: hits.append(8), lambda: hits.append(9), guard=g)
    g.engine_state("start")
    assert h.handle(DOWN, VK_A, 0) is True
    assert h.handle(DOWN, VK_F9, 0) is True and h.handle(UP, VK_F9, 0) is True
    assert h.handle(DOWN, VK_F8, 0) is True
    assert hits == [9, 8]
    assert h.handle(DOWN, VK_A, LLKHF_INJECTED) is False
    h2 = GlobalHotkeys(lambda: None, lambda: None)           # no guard: unchanged behaviour
    assert h2.handle(DOWN, VK_A, 0) is False


# ---------------- track ----------------
def test_track_counts_chars_enter_tab_backspace():
    g = tracking()
    for vk in (VK_A, 0x31, 0x20, 0xBE, 0x0D, 0x09):         # a 1 space . Enter Tab
        tap(g, vk)
    assert g.count == 6
    tap(g, 0x08)
    assert g.count == 5
    tap(g, VK_SHIFT)                                         # modifiers alone count nothing
    g.win_key(DOWN, VK_SHIFT, 0)
    tap(g, VK_A)                                             # 'A'
    g.win_key(UP, VK_SHIFT, 0)
    assert g.count == 6
    tap(g, 0x1B)                                             # Esc, F-keys: nothing
    tap(g, 0x70)
    assert g.count == 6
    er = g.take_erase()
    assert er == Erase(6, 0, True, "") and g.count == 0 and g.take_erase() is None


def test_track_auto_repeat_counts_each():
    g = tracking()
    for _ in range(4):
        g.win_key(DOWN, VK_A, 0)
    g.win_key(UP, VK_A, 0)
    assert g.count == 4


def test_backspace_floor_and_restore():
    g = tracking()
    tap(g, VK_A)
    for _ in range(3):
        tap(g, 0x08)
    assert g.count == 0 and g.restore == 2
    tap(g, VK_X)
    assert g.take_erase() == Erase(1, 2, True, "")


def test_shortcuts_not_counted_editing_shortcuts_distrust():
    g = tracking()
    g.win_key(DOWN, VK_LCTRL, 0)
    tap(g, 0x43)                                             # Ctrl+C: harmless, not counted
    tap(g, 0x53)                                             # Ctrl+S
    g.win_key(UP, VK_LCTRL, 0)
    g.win_key(DOWN, 0x5B, 0)
    tap(g, 0x44)                                             # Win+D
    g.win_key(UP, 0x5B, 0)
    assert g.count == 0 and g.trusted
    g.win_key(DOWN, VK_LCTRL, 0)
    tap(g, VK_V)                                             # Ctrl+V pastes an unknown amount
    g.win_key(UP, VK_LCTRL, 0)
    er = g.take_erase()
    assert not er.trusted and "shortcut" in er.reason


def test_altgr_produces_characters():
    g = tracking()
    g.win_key(DOWN, VK_LCTRL, 0)                             # AltGr = LCtrl + RAlt
    g.win_key(SYSDOWN, VK_RALT, 0)
    tap(g, 0x32)                                             # AltGr+2 = '@' on many layouts
    g.win_key(UP, VK_RALT, 0)
    g.win_key(UP, VK_LCTRL, 0)
    assert g.count == 1 and g.trusted


@pytest.mark.parametrize("vk", [0x25, 0x26, 0x27, 0x28, 0x24, 0x23, 0x21, 0x22, 0x2E, 0x2D])
def test_caret_keys_invalidate(vk):
    g = tracking()
    tap(g, VK_A)
    tap(g, vk)
    er = g.take_erase()
    assert not er.trusted and "caret" in er.reason
    assert g.trusted                                         # next pause starts trusted again


def test_dead_key_counts_once():
    chars = {0xBA: 0x80000000 | ord("^"), VK_A: ord("a")}    # '^' is a dead key on this layout
    g = tracking(char_of=lambda vk: chars.get(vk, 0))
    tap(g, 0xBA)
    tap(g, VK_A)                                             # -> 'â': one character
    assert g.count == 1
    tap(g, 0xC0)                                             # OEM key the layout says types nothing
    assert g.count == 2                                      # (vk class still counts OEM keys)


def test_mouse_clicks():
    owners = {}
    g = tracking(point_owner=lambda x, y: owners.get((x, y)))
    owners[(1, 1)], owners[(2, 2)], owners[(3, 3)] = "own", "other", "target"
    g.mouse_down(1, 1)                                       # our overlay: fine
    g.mouse_down(2, 2)                                       # another window: caret unchanged
    assert g.trusted
    g.mouse_down(3, 3)
    assert not g.trusted and g.reason == "mouse click"
    g2 = tracking()
    g2.mouse_down(5, 5)                                      # unknown owner: assume the worst
    assert not g2.trusted
    g3 = KeyGuard(True)
    g3.engine_state("start")
    g3.mouse_down(5, 5)                                      # only matters while tracking
    assert g3.trusted


def test_keys_outside_target_window_not_counted():
    front = [(1, 100)]
    f = FocusGuard(lambda: front[0], own_pid=999)
    g = KeyGuard(True, focus=f)
    g.engine_state("start")
    g.engine_state("paused")
    tap(g, VK_A)
    front[0] = (2, 200)                                      # user alt-tabbed away
    tap(g, VK_A)
    tap(g, VK_LEFT)
    front[0] = (1, 100)
    assert g.take_erase() == Erase(1, 0, True, "")


def test_counts_survive_quick_pause_resume_until_taken():
    g = tracking()
    tap(g, VK_A)
    g.engine_state("typing")
    g.engine_state("paused")                                 # paused again before the engine erased
    tap(g, VK_A)
    assert g.take_erase().count == 2
    g.give_back(Erase(1, 0))
    assert g.count == 1
    g.engine_state("start")                                  # a new run forgets everything
    assert g.count == 0


# ---------------- macOS logic ----------------
def test_mac_block_and_held_modifiers():
    g = KeyGuard(True)
    assert g.mac_key(hk.MAC_FLAGSCHANGED, 55, hk.MF_CMD) is False    # Cmd held before typing
    g.engine_state("start")
    assert g.mac_key(hk.MAC_KEYDOWN, 0) is True
    assert g.mac_key(hk.MAC_KEYUP, 0) is True
    assert g.mac_key(hk.MAC_KEYDOWN, 0, own=True) is False           # our own posted keys
    assert g.mac_key(hk.MAC_FLAGSCHANGED, 55, 0) is False            # Cmd released: passes
    assert g.mac_key(hk.MAC_FLAGSCHANGED, 56, hk.MF_SHIFT) is True   # Shift pressed: swallowed
    assert g.mac_key(hk.MAC_FLAGSCHANGED, 56, 0) is True


def test_mac_tracking():
    g = tracking()
    g.mac_key(hk.MAC_KEYDOWN, 0, 0, "a")
    g.mac_key(hk.MAC_KEYDOWN, 1, hk.MF_SHIFT, "S")
    g.mac_key(hk.MAC_KEYDOWN, 36, 0, "\r")                           # Return
    g.mac_key(hk.MAC_KEYDOWN, 48, 0, "\t")
    g.mac_key(hk.MAC_KEYDOWN, 122, 0, "")                      # F1: private-use char
    g.mac_key(hk.MAC_KEYDOWN, 8, hk.MF_CMD, "c")                     # Cmd+C: not counted
    assert g.count == 4
    g.mac_key(hk.MAC_KEYDOWN, 51, 0, "\x7f")                         # Delete (backspace)
    assert g.count == 3
    g.mac_key(hk.MAC_KEYDOWN, 14, hk.MF_ALT, "")                     # Option+e dead key
    g.mac_key(hk.MAC_KEYDOWN, 14, 0, "é")
    assert g.count == 4 and g.trusted
    g.mac_key(hk.MAC_KEYDOWN, 9, hk.MF_CMD, "v")                     # Cmd+V
    assert not g.trusted
    g2 = tracking()
    g2.mac_key(hk.MAC_KEYDOWN, 123, 0, "")                     # Left arrow
    assert not g2.trusted
    g3 = tracking()
    g3.mac_key(hk.MAC_KEYDOWN, 0, hk.MF_CTRL, "\x01")               # ^A: start of line in Cocoa
    assert not g3.trusted


class _Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


def _fake_quartz(fields, flags=0, text="", loc=(0, 0)):
    q = types.ModuleType("Quartz")
    q.CGEventGetIntegerValueField = lambda ev, f: fields.get(f, 0)
    q.CGEventGetFlags = lambda ev: flags
    q.CGEventKeyboardGetUnicodeString = lambda ev, n, a, b: (len(text), text)
    q.CGEventGetLocation = lambda ev: _Pt(*loc)
    return q


def test_mac_event_glue(monkeypatch):
    g = KeyGuard(True)
    hits = []
    h = GlobalHotkeys(lambda: hits.append(8), lambda: hits.append(9), guard=g)
    g.engine_state("start")
    monkeypatch.setitem(sys.modules, "Quartz", _fake_quartz({hk.MAC_FIELD_KEYCODE: 0}))
    assert h._mac_event(hk.MAC_KEYDOWN, object()) is True               # 'a' blocked
    monkeypatch.setitem(sys.modules, "Quartz", _fake_quartz(
        {hk.MAC_FIELD_KEYCODE: 0, hk.MAC_FIELD_SRC_PID: os.getpid()}))
    assert h._mac_event(hk.MAC_KEYDOWN, object()) is False              # ours
    monkeypatch.setitem(sys.modules, "Quartz", _fake_quartz({hk.MAC_FIELD_KEYCODE: hk.MAC_KC_F9}))
    assert h._mac_event(hk.MAC_KEYDOWN, object()) is True and hits == [9]
    g.engine_state("paused")
    monkeypatch.setitem(sys.modules, "Quartz", _fake_quartz({hk.MAC_FIELD_KEYCODE: 0}, text="a"))
    assert h._mac_event(hk.MAC_KEYDOWN, object()) is False and g.count == 1
    assert h._mac_event(1, object()) is False and not g.trusted         # left mouse down


def test_mac_point_owner_rects():
    owner = hk.mac_point_owner_for(lambda: [(0, 0, 100, 50)])
    assert owner(10, 10) == "own" and owner(200, 10) is None


# ---------------- Windows mouse hook only while tracking ----------------
def test_windows_mouse_listener_only_while_tracking(monkeypatch):
    monkeypatch.setattr(hk.plat, "IS_MAC", False)
    made = []

    class FakeMouse:
        def __init__(self, win32_event_filter=None):
            self.filter, self.stopped, self.daemon = win32_event_filter, False, False
            made.append(self)

        def start(self):
            pass

        def stop(self):
            self.stopped = True
    import pynput.mouse
    monkeypatch.setattr(pynput.mouse, "Listener", FakeMouse)
    g = KeyGuard(True, point_owner=lambda x, y: "target")
    h = GlobalHotkeys(lambda: None, lambda: None, guard=g)
    h.ok = True
    g.engine_state("start")
    assert not made
    g.engine_state("paused")
    assert len(made) == 1 and not made[0].stopped
    data = types.SimpleNamespace(flags=0, pt=types.SimpleNamespace(x=3, y=4))
    assert made[0].filter(0x200, data) is False and g.trusted        # mouse move: ignored
    assert made[0].filter(0x201, data) is False and not g.trusted    # click in the target
    g.engine_state("typing")
    assert made[0].stopped and h._mouse is None


# ---------------- engine: erase on resume ----------------
class _Harness:
    """Runs the engine on its thread; pauses after `at` characters and lets the test act as the user."""

    def __init__(self, text, at, guard, typo=0.0, start=True, **eng_kw):
        self.text = text
        self.fb = FakeBackend()
        self.logs, self.paused_ev, self.done = [], threading.Event(), threading.Event()
        self.result = []
        self.n = 0
        self.at = at

        def on_char(ch):
            self.n += 1
            if self.n == self.at:
                self.eng.pause()

        def on_status(k):
            if k == "paused":
                self.paused_ev.set()

        self.eng = TypingEngine(self.fb, Settings(typo_rate=typo, seed=5, revise_mode="end",
                                                  locate_mode="count"),
                                sleep=lambda s: None, on_char=on_char, on_status=on_status,
                                on_log=self.logs.append, on_finish=lambda r: (self.result.append(r),
                                                                               self.done.set()),
                                guard=guard, **eng_kw)
        if start:
            self.eng.start(text)

    def user_types(self, g, s):
        vks = {"x": VK_X, "y": 0x59, "z": 0x5A, " ": 0x20}
        for ch in s:
            self.fb.type_char(ch)
            tap(g, vks[ch])

    def user_backspaces(self, g, n):
        for _ in range(n):
            self.fb.backspace()
            tap(g, 0x08)


def test_engine_erases_user_chars_on_resume():
    g = KeyGuard(True)
    text = "the quick brown fox jumps over the lazy dog"
    h = _Harness(text, 10, g)
    assert h.paused_ev.wait(5)
    assert g.state == G_TRACK
    h.user_types(g, "xyz z")
    assert h.fb.text.endswith("xyz z")
    h.eng.resume()
    assert g.state == G_BLOCK
    assert h.done.wait(10)
    assert h.result == ["done"] and h.fb.text == text
    assert "erased 5 chars typed while paused" in h.logs
    assert g.state == G_OFF


def test_engine_restores_deleted_chars_with_typos():
    g = KeyGuard(True)
    text = "hello world, this is a longer sentence with typos in it " * 3
    h = _Harness(text, 25, g, typo=0.08)
    assert h.paused_ev.wait(5)
    h.user_types(g, "xy")
    h.user_backspaces(g, 5)                     # 2 own chars + 3 of the engine's text
    h.user_types(g, "z")
    h.eng.resume()
    assert h.done.wait(10)
    assert h.fb.text == text, h.logs
    assert any("re-typed 3 chars" in s for s in h.logs)


def test_engine_untrusted_erases_nothing():
    g = KeyGuard(True)
    h = _Harness("abcdefghijklmnop", 5, g)
    assert h.paused_ev.wait(5)
    h.user_types(g, "x")
    tap(g, VK_LEFT)                             # caret moved: never erase blindly
    h.eng.resume()
    assert h.done.wait(10)
    assert h.fb.text == "abcdexfghijklmnop"          # left as it is, nothing deleted blindly
    assert any("nothing erased" in s for s in h.logs)
    assert not any(s.startswith("erased") for s in h.logs)


def test_engine_without_guard_unchanged():
    fb = FakeBackend()
    eng = TypingEngine(fb, Settings(typo_rate=0.05, seed=2), sleep=lambda s: None)
    assert eng.run("plain [a][b] text") and fb.text == "plain b text"


def test_base_guard_protocol_is_noop():
    fb = FakeBackend()
    eng = TypingEngine(fb, Settings(typo_rate=0, seed=2), sleep=lambda s: None, guard=Guard())
    assert eng.run("abc") and fb.text == "abc"


# ---------------- focus guard ----------------
def test_focus_guard_unit():
    front = [(7, 70)]
    f = FocusGuard(lambda: front[0], own_pid=1)
    assert f.lost() is None                     # not armed
    assert f.arm() and f.lost() is None
    front[0] = (8, 80)
    assert f.lost() == "target window lost focus"
    front[0] = (9, 1)
    assert "Human Typer" in f.lost()
    front[0] = None
    assert "nothing in front" in f.lost()
    front[0] = (9, 1)
    assert not f.arm() and f.lost() is None     # our own window in front: not armed
    f2 = FocusGuard(lambda: front[0], own_pid=1, live=False)
    front[0] = (7, 70)
    assert not f2.arm()                         # nothing cached yet
    f2.refresh()
    assert f2.arm()
    front[0] = (8, 80)
    assert f2.lost() is None                    # cached value until the next refresh()
    f2.refresh()
    assert f2.lost()
    f3 = FocusGuard()
    f3.front = None                             # no way to ask the OS: guard stays idle
    assert not f3.available and not f3.arm() and f3.lost() is None


def test_focus_guard_pauses_engine_on_foreground_change():
    front = [(7, 70)]
    f = FocusGuard(lambda: front[0], own_pid=1)
    g = KeyGuard(True, focus=f)
    text = "some text that is long enough to switch windows in the middle of it"
    h = _Harness(text, 10 ** 9, g, start=False)   # never pauses by itself
    h.eng.guard_interval = 0.0
    orig = h.fb.type_char
    count = [0]

    def type_char(ch):
        orig(ch)
        count[0] += 1
        if count[0] == 8:
            front[0] = (8, 80)                  # another window comes to the front
    h.fb.type_char = type_char
    h.eng.start(text)
    assert h.paused_ev.wait(5)
    assert "target window lost focus — paused" in h.logs
    assert g.state == G_TRACK
    typed = len(h.fb.text)
    assert typed <= 9                           # at most the key in flight after the switch
    h.paused_ev.clear()
    h.eng.resume()                              # still the wrong window: pauses again
    assert h.paused_ev.wait(5)
    assert h.logs.count("target window lost focus — paused") == 2 and len(h.fb.text) == typed
    front[0] = (7, 70)
    h.eng.resume()
    assert h.done.wait(10)
    assert h.fb.text == text


def test_make_guard_platform():
    g = hk.make_guard(True)
    assert isinstance(g, KeyGuard) and g.enabled and g.focus is not None


def test_config_default_off():
    from humantyper import config
    assert config.DEFAULTS["keyboard_guard"] is False
