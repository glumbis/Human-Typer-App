"""Global hotkeys (F8 start/stop, F9 pause/resume) that are suppressed system-wide.

Also feeds the optional KEYBOARD GUARD (KeyGuard: block keys while typing, count what the user
types while paused) and the FOCUS GUARD (FocusGuard: pause when the target window loses focus).

Windows: pynput's low-level keyboard hook. macOS: our own Quartz CGEventTap on a private CFRunLoop
thread (MacEventTap). pynput's mac Listener is NOT used: its thread looks up the keyboard layout via
TISCopyCurrentKeyboardInputSource/TSMGetInputSourceProperty, which macOS 14+ only allows on the main
thread; anywhere else the process is killed (EXC_BREAKPOINT in dispatch_assert_queue_fail)."""
import os
import threading
from typing import Callable, Optional

from . import plat

VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79         # REVIEW FIELD
LLKHF_INJECTED = 0x10
_DOWN = (0x100, 0x104)   # WM_KEYDOWN, WM_SYSKEYDOWN
_UP = (0x101, 0x105)     # WM_KEYUP, WM_SYSKEYUP

# macOS (kVK_ANSI codes, CGEvent types/fields)
MAC_KC_F8 = 100          # kVK_F8
MAC_KC_F9 = 101          # kVK_F9
MAC_KC_F10 = 109         # kVK_F10 (REVIEW FIELD)
MAC_KEYDOWN = 10         # kCGEventKeyDown
MAC_KEYUP = 11           # kCGEventKeyUp
MAC_FIELD_KEYCODE = 9    # kCGKeyboardEventKeycode
MAC_FIELD_AUTOREPEAT = 8  # kCGKeyboardEventAutorepeat
MAC_FIELD_SRC_PID = 41   # kCGEventSourceUnixProcessID
MAC_TAP_DISABLED = (0xFFFFFFFE, 0xFFFFFFFF)   # kCGEventTapDisabledByTimeout / ...ByUserInput
# keyDown, keyUp, flagsChanged (modifiers, for the KEYBOARD GUARD) and mouse-downs (caret moves)
MAC_KEY_MASK = ((1 << MAC_KEYDOWN) | (1 << MAC_KEYUP) | (1 << 12)
                | (1 << 1) | (1 << 3) | (1 << 25))
TAP_HELP = ("grant Accessibility or Input Monitoring (System Settings > Privacy & Security); "
            "hotkeys switch on by themselves within a few seconds")


def mac_handle(held: set, callbacks: dict, event_type: int, keycode: int,
               autorepeat: int = 0, src_pid: int = 0, own_pid: int = -1) -> bool:
    """Pure macOS logic. Returns True when the event must be suppressed.
    Note: with the default Mac keyboard setting F8/F9 are media keys (NSSystemDefined
    events, never seen here): press fn+F8 / fn+F9 or enable "Use F1, F2, etc. as
    standard function keys"."""
    if keycode not in callbacks:
        return False
    if src_pid and src_pid == own_pid:
        return False                          # keystrokes injected by this process
    if event_type == MAC_KEYDOWN:
        if autorepeat or keycode in held:     # ignore auto-repeat
            return True
        held.add(keycode)
        try:
            callbacks[keycode]()
        except Exception:
            pass
        return True
    if event_type == MAC_KEYUP:
        held.discard(keycode)
        return True
    return False


def mac_event_text(Q, event) -> str:
    """The characters a keyDown event types (CGEventKeyboardGetUnicodeString: a plain event field,
    no keyboard-layout lookup)."""
    try:
        r = Q.CGEventKeyboardGetUnicodeString(event, 4, None, None)
    except Exception:
        return ""
    if isinstance(r, tuple):
        r = next((x for x in r if isinstance(x, str)), "")
    return r if isinstance(r, str) else ""


class MacEventTap:
    """Active keyboard CGEventTap (keyDown/keyUp only) running on its own CFRunLoop thread.

    handler(event_type, event) -> True to swallow the event. The callback only reads integer fields
    of the event, so no keyboard-layout (TSM) API is ever touched off the main thread. Without the
    Accessibility / Input Monitoring permission CGEventTapCreate returns NULL: start() raises."""

    def __init__(self, handler, quartz=None):
        self.handler = handler
        self.Q = quartz
        self._tap = None
        self._loop = None
        self._thread = None
        self._cb = None                       # keep the Python callback alive
        self.error = ""
        self.events_seen = 0                  # diagnostics (--e2e / --diag)
        self.last_event = None
        self.mode = None                      # "active" | "listen" once running

    def _callback(self, proxy, etype, event, refcon):
        self.events_seen += 1
        try:
            if int(etype) in MAC_TAP_DISABLED:          # macOS disabled us (slow callback): re-arm
                if self._tap is not None:
                    self.Q.CGEventTapEnable(self._tap, True)
                return event
            if self.handler(int(etype), event):
                self.last_event = (int(etype), "handled")
                return None
        except Exception:
            pass
        return event

    def _create(self):
        """Create the tap: an active (event-swallowing) tap needs Accessibility; if macOS refuses it,
        fall back to a listen-only tap, which only needs Input Monitoring (F8 then also reaches the
        target app, which ignores it). Returns (tap, mode) or (None, None) without any permission."""
        Q = self.Q
        opts = [(Q.kCGEventTapOptionDefault, "active"),
                (getattr(Q, "kCGEventTapOptionListenOnly", 1), "listen")]
        for opt, mode in opts:
            tap = Q.CGEventTapCreate(Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap, opt, MAC_KEY_MASK,
                                     self._cb, None)
            if tap is not None:
                return tap, mode
        return None, None

    def _run(self, ready):
        Q = self.Q
        try:
            self._cb = self._callback
            tap, mode = self._create()
            if tap is None:
                self.error = TAP_HELP
                return
            src = Q.CFMachPortCreateRunLoopSource(None, tap, 0)
            loop = Q.CFRunLoopGetCurrent()
            Q.CFRunLoopAddSource(loop, src, Q.kCFRunLoopCommonModes)
            Q.CGEventTapEnable(tap, True)
            self._tap, self._loop, self.mode = tap, loop, mode
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            return
        finally:
            ready.set()
        Q.CFRunLoopRun()

    def start(self, timeout=3.0):
        if self.Q is None:
            import Quartz
            self.Q = Quartz
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="ht-hotkeys", daemon=True)
        self._thread.start()
        if not ready.wait(timeout):
            self.error = "event tap did not start"
        if self._loop is None:
            raise RuntimeError(self.error or TAP_HELP)

    def stop(self):
        tap, loop = self._tap, self._loop
        self._tap = self._loop = None
        try:
            if tap is not None:
                self.Q.CGEventTapEnable(tap, False)
            if loop is not None:
                self.Q.CFRunLoopStop(loop)
        except Exception:
            pass


# ---- KEYBOARD GUARD ---------------------------------------------------------------------------
# While typing: physical keys are swallowed (block). While paused: keys pass and the net
# characters the user added at the caret are counted (track), so the engine can press them away
# with Backspace before it continues. Pure logic (KeyGuard.win_key / mac_key / mouse_down) is fed
# by the hook threads; the engine drives the state through the engine.Guard protocol.
G_OFF, G_BLOCK, G_TRACK = "off", "block", "track"

# Windows virtual keys
VK_BACK, VK_TAB, VK_RETURN, VK_SPACE = 0x08, 0x09, 0x0D, 0x20
VK_PROCESSKEY = 0xE5                                       # IME composition
_W_SHIFT = frozenset((0x10, 0xA0, 0xA1))
_W_CTRL = frozenset((0x11, 0xA2, 0xA3))
_W_ALT = frozenset((0x12, 0xA4, 0xA5))
_W_WIN = frozenset((0x5B, 0x5C))
_W_MODS = _W_SHIFT | _W_CTRL | _W_ALT | _W_WIN | frozenset((0x14, 0x90, 0x91))   # + Caps/Num/Scroll lock
# PgUp PgDn End Home Left Up Right Down, Insert (overwrite mode / Shift+Ins paste), Delete
_W_CARET = frozenset(range(0x21, 0x29)) | frozenset((0x2D, 0x2E))
# keys that type a character on (nearly) every layout: space, 0-9, A-Z, numpad 0-9 * + sep - . /,
# OEM punctuation keys. Layout-specific: see KeyGuard.char_of (MapVirtualKeyEx, dead keys).
_W_PRINT = (frozenset((VK_SPACE, 0xE2)) | frozenset(range(0x30, 0x3A)) | frozenset(range(0x41, 0x5B))
            | frozenset(range(0x60, 0x70)) | frozenset(range(0xBA, 0xC1)) | frozenset(range(0xDB, 0xE0)))
_W_NUMPAD_DIGITS = frozenset(range(0x60, 0x6A))
# Ctrl/Alt/Win + these edit the text in ways we cannot count: paste, cut, undo, redo, select all,
# delete word
_W_EDIT_SHORTCUT = frozenset((0x56, 0x58, 0x5A, 0x59, 0x41, VK_BACK, 0x2E))
MAPVK_DEAD = 0x80000000

# macOS (kVK_* key codes, CGEventFlags)
MAC_FLAGSCHANGED = 12
MAC_MOUSE_DOWN = (1, 3, 25)              # kCGEventLeftMouseDown / RightMouseDown / OtherMouseDown
MF_SHIFT, MF_CTRL, MF_ALT, MF_CMD = 0x20000, 0x40000, 0x80000, 0x100000
_M_MOD_FLAG = {56: MF_SHIFT, 60: MF_SHIFT, 59: MF_CTRL, 62: MF_CTRL, 58: MF_ALT, 61: MF_ALT,
               55: MF_CMD, 54: MF_CMD}        # Caps Lock / fn are never blocked (toggle state)
MAC_KC_DELETE, MAC_KC_RETURN, MAC_KC_ENTER, MAC_KC_TAB = 51, 36, 76, 48
_M_CARET = frozenset((123, 124, 125, 126, 115, 119, 116, 121, 117))   # arrows, Home End PgUp PgDn, fwd-del
_M_EDIT_SHORTCUT = frozenset((9, 7, 6, 0, 16, MAC_KC_DELETE))           # Cmd+V X Z A Y, Cmd+Delete
_M_OPT_DEAD = frozenset((14, 32, 34, 45, 50))                          # Option+e u i n ` (US layout)


def _mac_printable(text) -> bool:
    if not text:
        return False
    for ch in text:
        o = ord(ch)
        if o < 0x20 or o == 0x7F or 0xF700 <= o <= 0xF8FF:     # control / function-key private use
            return False
    return True


class KeyGuard:
    """KEYBOARD GUARD state machine (implements the engine.Guard protocol).

    off   -> nothing touched (also while the setting is off: enabled=False)
    block -> physical key downs/ups are swallowed. Ups of keys that went down while not blocking
             pass (so nothing stays stuck in the target app); injected keys always pass
    track -> keys pass; net characters added at the caret are counted (printable/Enter/Tab +1,
             Backspace -1 or, past what the user typed, +1 to `restore`). Caret moves (arrows,
             Home/End/PgUp/PgDn, Delete, Insert, mouse clicks in the target, editing shortcuts,
             Alt codes, IME) make the count untrustworthy: then nothing is erased on resume.

    Engine states: "start" -> block (counters reset, focus guard armed), "typing" -> block,
    "paused" -> track, "idle" -> off. Counters survive pause/resume until take_erase() hands them
    to the engine. All methods are thread-safe and the key paths never block (hook timeouts).

    Injectable: focus (FocusGuard, or None), char_of(vk) -> MapVirtualKeyEx VK_TO_CHAR value
    (0 = no character, high bit = dead key; None = use the vk class only), point_owner(x, y) ->
    "own" | "target" | "other" | None for mouse clicks."""

    def __init__(self, enabled: bool = False, focus=None, char_of=None, point_owner=None):
        self.enabled = enabled
        self.focus = focus
        self.char_of = char_of
        self.point_owner = point_owner
        self._lock = threading.Lock()
        self.state = G_OFF
        self.listeners = []          # callables(state) run after every state change
        self._free = set()           # keys that went down while not blocking: their up passes
        self._mods = set()           # Windows: modifier vks physically held
        self._reset()
        self.blocked = 0             # diagnostics: keys swallowed

    def _reset(self):
        self.count = 0
        self.restore = 0
        self.trusted = True
        self.reason = ""
        self._dead = False

    # ---- engine.Guard protocol --------------------------------------------------------------
    def engine_state(self, state: str) -> None:
        new = {"start": G_BLOCK, "typing": G_BLOCK, "paused": G_TRACK}.get(state, G_OFF)
        if not self.enabled:
            new = G_OFF
        with self._lock:
            if state == "start":
                self._reset()
            old, self.state = self.state, new
            if new == G_TRACK:
                self._dead = False
        f = self.focus
        if f is not None:
            try:
                if state == "start":
                    f.arm()
                elif state == "idle":
                    f.disarm()
            except Exception:
                pass
        if new != old:
            for cb in list(self.listeners):
                try:
                    cb(new)
                except Exception:
                    pass

    def take_erase(self):
        from .engine import Erase
        with self._lock:
            er = Erase(self.count, self.restore, self.trusted, self.reason)
            self._reset()
        if er.count or er.restore or not er.trusted:
            return er
        return None

    def give_back(self, er) -> None:
        with self._lock:
            self.count += max(0, int(er.count))
            self.restore += max(0, int(er.restore))

    def focus_lost(self):
        f = self.focus
        return f.lost() if (f is not None and self.enabled) else None

    # ---- tracking helpers (lock held) -------------------------------------------------------
    def _distrust(self, why: str) -> None:
        if self.trusted:
            self.trusted = False
            self.reason = why

    def _add(self) -> None:
        self.count += 1
        self._dead = False

    def _back(self) -> None:
        self._dead = False
        if self.count > 0:
            self.count -= 1
        else:
            self.restore += 1              # deleting into the text we typed

    def _in_target(self) -> bool:
        f = self.focus
        if f is None:
            return True
        try:
            return f.in_target()
        except Exception:
            return True

    # ---- Windows (low-level keyboard hook) --------------------------------------------------
    def win_key(self, msg: int, vk: int, flags: int) -> bool:
        """True = swallow. Called for every key except F8/F9 (GlobalHotkeys handles those)."""
        if not self.enabled or flags & LLKHF_INJECTED:
            return False
        down = msg in _DOWN
        if not down and msg not in _UP:
            return False
        with self._lock:
            if vk in _W_MODS:
                (self._mods.add if down else self._mods.discard)(vk)
            if self.state == G_BLOCK:
                if down:
                    self.blocked += 1
                    return True
                if vk in self._free:
                    self._free.discard(vk)
                    return False
                return True
            if down:
                self._free.add(vk)
            else:
                self._free.discard(vk)
                return False
            if self.state == G_TRACK and self._in_target():
                self._win_track(vk)
        return False

    def _win_track(self, vk: int) -> None:
        if vk in _W_MODS:
            return
        m = self._mods
        ctrl, alt, win = bool(m & _W_CTRL), bool(m & _W_ALT), bool(m & _W_WIN)
        altgr = ctrl and alt and not win              # AltGr = LCtrl + RAlt (or Ctrl+Alt)
        if vk in _W_CARET:
            self._distrust("caret moved (arrow/Home/End/PgUp/PgDn/Delete/Insert)")
            return
        if vk == VK_PROCESSKEY:
            self._distrust("IME input")
            return
        if (ctrl or alt or win) and not altgr:
            if alt and not ctrl and vk in _W_NUMPAD_DIGITS:
                self._distrust("Alt code typed")
            elif vk in _W_EDIT_SHORTCUT:
                self._distrust("editing shortcut used")
            return                                     # other shortcuts: not counted
        if vk == VK_BACK:
            self._back()
        elif vk in (VK_RETURN, VK_TAB):
            self._add()
        else:
            ch = 0
            if self.char_of is not None:
                try:
                    ch = int(self.char_of(vk) or 0)
                except Exception:
                    ch = 0
            if ch & MAPVK_DEAD:
                self._dead = True                      # dead key: the next key makes one character
            elif ch or vk in _W_PRINT:
                self._add()

    # ---- macOS (CGEventTap) -----------------------------------------------------------------
    def mac_key(self, event_type: int, keycode: int, flags: int = 0, text: str = "",
                own: bool = False) -> bool:
        """True = swallow. keyDown/keyUp/flagsChanged except F8/F9. `text` is the event's unicode
        string (CGEventKeyboardGetUnicodeString; only needed for keyDown while tracking)."""
        if not self.enabled or own:
            return False
        if event_type == MAC_FLAGSCHANGED:
            bit = _M_MOD_FLAG.get(keycode)
            if not bit:
                return False
            down = bool(flags & bit)
        elif event_type in (MAC_KEYDOWN, MAC_KEYUP):
            down = event_type == MAC_KEYDOWN
        else:
            return False
        key = ("m", keycode) if event_type == MAC_FLAGSCHANGED else keycode
        with self._lock:
            if self.state == G_BLOCK:
                if down:
                    self.blocked += 1
                    return True
                if key in self._free:
                    self._free.discard(key)
                    return False
                return True
            if down:
                self._free.add(key)
            else:
                self._free.discard(key)
                return False
            if self.state == G_TRACK and event_type == MAC_KEYDOWN and self._in_target():
                self._mac_track(keycode, flags, text)
        return False

    def _mac_track(self, kc: int, flags: int, text: str) -> None:
        if kc in _M_CARET:
            self._distrust("caret moved (arrow/Home/End/PgUp/PgDn/forward delete)")
            return
        if flags & MF_CTRL:                            # Cocoa emacs keys (^A ^E ^K ^D ^H ...)
            self._distrust("Control shortcut used")
            return
        if flags & MF_CMD:
            if kc in _M_EDIT_SHORTCUT:
                self._distrust("editing shortcut used")
            return
        if kc == MAC_KC_DELETE:
            self._back()
        elif kc in (MAC_KC_RETURN, MAC_KC_ENTER, MAC_KC_TAB):
            self._add()
        elif flags & MF_ALT and kc in _M_OPT_DEAD:
            self._dead = True
        elif _mac_printable(text):
            self._add()

    # ---- mouse (both) -----------------------------------------------------------------------
    def mouse_down(self, x, y) -> None:
        """A physical mouse button went down. While tracking, a click that may land in the target
        window moves the caret: stop trusting the count. Clicks on our own windows are fine."""
        if not self.enabled or self.state != G_TRACK:
            return
        owner = None
        if self.point_owner is not None:
            try:
                owner = self.point_owner(x, y)
            except Exception:
                owner = None
        if owner in ("own", "other"):
            return
        with self._lock:
            self._distrust("mouse click")


# ---- FOCUS GUARD ------------------------------------------------------------------------------
class FocusGuard:
    """Remembers the foreground window when typing starts; lost() tells when another window (or one
    of ours) is in front. front() -> (window_key, pid) or None (nothing in front / unknown).
    live=False: lost()/in_target() use the value cached by refresh() (macOS: NSWorkspace is only
    asked on the Tk main thread)."""

    def __init__(self, front=None, own_pid=None, live=True):
        self.front = front if front is not None else _platform_front()
        self.live = live
        self.own_pid = os.getpid() if own_pid is None else own_pid
        self.target = None
        self._cached = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.front is not None

    def _now(self):
        if self.front is None:
            return None
        if not self.live:
            return self._cached
        try:
            return self.front()
        except Exception:
            return None

    def refresh(self) -> None:
        """Cache the current front window (call regularly from the thread that may ask the OS)."""
        if self.front is None:
            return
        try:
            self._cached = self.front()
        except Exception:
            self._cached = None

    def arm(self) -> bool:
        """Remember the current front window as the target. False (guard idle) if unknown or ours."""
        cur = self._now()
        ok = cur is not None and cur[1] != self.own_pid
        self.target = cur if ok else None
        return ok

    def disarm(self) -> None:
        self.target = None

    def lost(self):
        """Reason string when typing must not go on, else None (also while not armed)."""
        t = self.target
        if t is None:
            return None
        cur = self._now()
        if cur is None:
            return "target window lost focus (nothing in front)"
        if cur[1] == self.own_pid:
            return "target window lost focus (Human Typer window in front)"
        if cur[0] != t[0]:
            return "target window lost focus"
        return None

    def in_target(self) -> bool:
        return self.lost() is None


# ---- platform glue ------------------------------------------------------------------------------
_U32 = None


def _user32():
    global _U32
    if _U32 is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetAncestor.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.WindowFromPoint.restype = wintypes.HWND
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.GetKeyboardLayout.restype = ctypes.c_void_p
        u.GetKeyboardLayout.argtypes = [wintypes.DWORD]
        u.MapVirtualKeyExW.restype = ctypes.c_uint
        u.MapVirtualKeyExW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        _U32 = u
    return _U32


def _win_root(hwnd):
    if not hwnd:
        return None
    return _user32().GetAncestor(hwnd, 3) or hwnd          # GA_ROOTOWNER: owned popups -> owner


def _win_pid(hwnd) -> int:
    import ctypes
    from ctypes import wintypes
    pid = wintypes.DWORD(0)
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def win_front():
    """(root-owner HWND, pid) of the foreground window, None if there is none."""
    root = _win_root(_user32().GetForegroundWindow())
    return (int(root), _win_pid(root)) if root else None


def win_char_of(vk: int) -> int:
    """MapVirtualKeyEx(MAPVK_VK_TO_CHAR) in the foreground window's keyboard layout: the unshifted
    character (0 = none, high bit = dead key). Unlike ToUnicodeEx it has no side effects on the
    dead-key state of the target app."""
    u = _user32()
    tid = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
    return int(u.MapVirtualKeyExW(vk, 2, u.GetKeyboardLayout(tid)))


def win_point_owner_for(focus):
    """point_owner(x, y) for KeyGuard on Windows: which window a click at screen pixel (x, y) hits."""
    def owner(x, y):
        from ctypes import wintypes
        root = _win_root(_user32().WindowFromPoint(wintypes.POINT(int(x), int(y))))
        if not root:
            return None
        if _win_pid(root) == os.getpid():
            return "own"
        t = focus.target if focus is not None else None
        if t is None:
            return None
        return "target" if int(root) == t[0] else "other"
    return owner


def mac_front_fn():
    """front() for FocusGuard on macOS: (pid, pid) of the frontmost app (NSWorkspace), or None
    without pyobjc/AppKit. Only call it on the main thread (FocusGuard(live=False) + refresh())."""
    try:
        from AppKit import NSWorkspace
        ws = NSWorkspace.sharedWorkspace()
    except Exception:
        return None

    def front():
        app = ws.frontmostApplication()
        if app is None:
            return None
        pid = int(app.processIdentifier())
        return (pid, pid)
    return front


def mac_point_owner_for(get_rects):
    """point_owner for KeyGuard on macOS: "own" inside one of our window rectangles (x0, y0, x1, y1
    in global points, kept up to date by the Tk thread), else None (may be the target)."""
    def owner(x, y):
        for x0, y0, x1, y1 in list(get_rects() or ()):
            if x0 <= x < x1 and y0 <= y < y1:
                return "own"
        return None
    return owner


def _platform_front():
    if plat.IS_MAC:
        return mac_front_fn()
    if os.name == "nt":
        return win_front
    return None


def make_guard(enabled: bool = False, get_own_rects=None) -> KeyGuard:
    """KeyGuard + FocusGuard wired for this platform."""
    if plat.IS_MAC:
        focus = FocusGuard(mac_front_fn(), live=False)
        return KeyGuard(enabled, focus, None, mac_point_owner_for(get_own_rects or (lambda: ())))
    if os.name == "nt":
        focus = FocusGuard(win_front, live=True)
        return KeyGuard(enabled, focus, win_char_of, win_point_owner_for(focus))
    return KeyGuard(enabled, FocusGuard(None))


class GlobalHotkeys:
    """F8/F9, optional F10 (REVIEW FIELD) (more hotkeys: add vk -> callback to _callbacks and
    keycode -> callback to _mac_callbacks; they are swallowed and never reach the guard).
    guard: optional KeyGuard that sees every other key (and, while tracking, mouse-downs)."""

    def __init__(self, on_f8: Callable[[], None], on_f9: Callable[[], None], guard=None,
                 on_f10: Optional[Callable[[], None]] = None):
        self._callbacks = {VK_F8: on_f8, VK_F9: on_f9}
        self._mac_callbacks = {MAC_KC_F8: on_f8, MAC_KC_F9: on_f9}
        if on_f10 is not None:
            self._callbacks[VK_F10] = on_f10
            self._mac_callbacks[MAC_KC_F10] = on_f10
        self._held = set()
        self._listener = None
        self._mouse = None                    # Windows: mouse hook, only while the guard tracks
        self._mouse_lock = threading.Lock()
        self.ok = False
        self.error = ""
        self.guard = None
        if guard is not None:
            self.set_guard(guard)

    def set_guard(self, guard) -> None:
        self.guard = guard
        if guard is not None and self._guard_changed not in guard.listeners:
            guard.listeners.append(self._guard_changed)

    def _guard_changed(self, state) -> None:
        """Windows: run a low-level mouse hook only while tracking (clicks move the caret)."""
        if plat.IS_MAC:
            return                            # mouse-downs come through the event tap
        with self._mouse_lock:
            if state == G_TRACK and self.ok and self._mouse is None:
                try:
                    from pynput import mouse
                    m = mouse.Listener(win32_event_filter=self._mouse_filter)
                    m.daemon = True
                    m.start()
                    self._mouse = m
                except Exception:
                    self._mouse = None
            elif state != G_TRACK and self._mouse is not None:
                m, self._mouse = self._mouse, None
                try:
                    m.stop()
                except Exception:
                    pass

    def _mouse_filter(self, msg, data):
        if msg in (0x201, 0x204, 0x207, 0x20B) and not (data.flags & 0x3):   # button down, physical
            g = self.guard
            if g is not None:
                g.mouse_down(data.pt.x, data.pt.y)
        return False                          # never needs pynput's own callbacks

    def handle(self, msg: int, vk: int, flags: int) -> bool:
        """Pure logic. Returns True when the event must be suppressed."""
        if vk not in self._callbacks:
            g = self.guard
            return g.win_key(msg, vk, flags) if g is not None else False
        if flags & LLKHF_INJECTED:
            return False                      # our own (or other software's) keystrokes
        if msg in _DOWN:
            if vk not in self._held:          # ignore auto-repeat
                self._held.add(vk)
                try:
                    self._callbacks[vk]()
                except Exception:
                    pass
            return True
        if msg in _UP:
            self._held.discard(vk)
            return True
        return False

    def _filter(self, msg, data):
        if self.handle(msg, data.vkCode, data.flags):
            self._listener.suppress_event()   # raises; event never reaches other apps
        return False                          # we never need on_press/on_release

    def _mac_event(self, event_type, event) -> bool:
        """MacEventTap handler: True = swallow the event. Reads only event fields: no keyboard
        layout (TSM/TIS) API is touched on this thread."""
        import Quartz
        g = self.guard
        if event_type in MAC_MOUSE_DOWN:
            if g is not None and g.enabled and g.state == G_TRACK:
                pt = Quartz.CGEventGetLocation(event)
                g.mouse_down(pt.x, pt.y)
            return False
        kc = int(Quartz.CGEventGetIntegerValueField(event, MAC_FIELD_KEYCODE))
        if kc not in self._mac_callbacks:
            if g is None or not g.enabled or g.state == G_OFF:
                return False
            pid = int(Quartz.CGEventGetIntegerValueField(event, MAC_FIELD_SRC_PID))
            flags = int(Quartz.CGEventGetFlags(event))
            text = ""
            if event_type == MAC_KEYDOWN and g.state == G_TRACK:
                text = mac_event_text(Quartz, event)
            return g.mac_key(int(event_type), kc, flags, text, own=bool(pid) and pid == os.getpid())
        if event_type == MAC_FLAGSCHANGED:
            return False
        rep = Quartz.CGEventGetIntegerValueField(event, MAC_FIELD_AUTOREPEAT)
        pid = Quartz.CGEventGetIntegerValueField(event, MAC_FIELD_SRC_PID)
        return mac_handle(self._held, self._mac_callbacks, int(event_type), kc,
                          int(rep), int(pid), os.getpid())

    def start(self) -> None:
        """Start the listener. Raises RuntimeError with a readable message on failure
        (also sets .ok / .error)."""
        self.ok, self.error = False, ""
        try:
            if plat.IS_MAC:
                self._listener = MacEventTap(self._mac_event)
                self._listener.start()
            else:
                from pynput import keyboard
                self._listener = keyboard.Listener(win32_event_filter=self._filter)
                self._listener.daemon = True
                self._listener.start()
            self.ok = True
        except Exception as e:
            self.error = str(e) or type(e).__name__
            self._listener = None
            raise RuntimeError(f"Global hotkeys unavailable: {self.error}") from e

    @property
    def mode(self) -> Optional[str]:
        """"active" / "listen" (macOS event tap), "hook" (Windows), None while off."""
        if not self.ok:
            return None
        return getattr(self._listener, "mode", None) or "hook"

    def retry(self, upgrade: bool = False) -> bool:
        """Try again to switch the hotkeys on (macOS: after the user granted the permission, no
        restart needed). upgrade=True also replaces a listen-only tap by an active one (which keeps
        F8/F9 away from the target app) once that is possible. Returns True while hotkeys work."""
        if self.ok and not (upgrade and self.mode == "listen"):
            return True
        old, was_ok = self._listener, self.ok
        try:
            if was_ok:
                self._listener = None
                self._held.clear()
            self.start()
        except RuntimeError:
            if was_ok:                        # keep the working listen-only tap
                self._listener, self.ok, self.error = old, True, ""
            return self.ok
        if was_ok and old is not None:
            if self.mode == "listen":         # no better: keep the old one, drop the new one
                self._listener.stop()
                self._listener = old
            else:
                old.stop()
        return True

    def stop(self) -> None:
        self.ok = False
        self._guard_changed(G_OFF)
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
