"""Global hotkeys (F8 start/stop, F9 pause/resume) that are suppressed system-wide.

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
LLKHF_INJECTED = 0x10
_DOWN = (0x100, 0x104)   # WM_KEYDOWN, WM_SYSKEYDOWN
_UP = (0x101, 0x105)     # WM_KEYUP, WM_SYSKEYUP

# macOS (kVK_ANSI codes, CGEvent types/fields)
MAC_KC_F8 = 100          # kVK_F8
MAC_KC_F9 = 101          # kVK_F9
MAC_KEYDOWN = 10         # kCGEventKeyDown
MAC_KEYUP = 11           # kCGEventKeyUp
MAC_FIELD_KEYCODE = 9    # kCGKeyboardEventKeycode
MAC_FIELD_AUTOREPEAT = 8  # kCGKeyboardEventAutorepeat
MAC_FIELD_SRC_PID = 41   # kCGEventSourceUnixProcessID
MAC_TAP_DISABLED = (0xFFFFFFFE, 0xFFFFFFFF)   # kCGEventTapDisabledByTimeout / ...ByUserInput
MAC_KEY_MASK = (1 << MAC_KEYDOWN) | (1 << MAC_KEYUP)
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


class GlobalHotkeys:
    def __init__(self, on_f8: Callable[[], None], on_f9: Callable[[], None]):
        self._callbacks = {VK_F8: on_f8, VK_F9: on_f9}
        self._mac_callbacks = {MAC_KC_F8: on_f8, MAC_KC_F9: on_f9}
        self._held = set()
        self._listener = None
        self.ok = False
        self.error = ""

    def handle(self, msg: int, vk: int, flags: int) -> bool:
        """Pure logic. Returns True when the event must be suppressed."""
        if vk not in self._callbacks:
            return False
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
        """MacEventTap handler: True = swallow the event."""
        import Quartz
        kc = Quartz.CGEventGetIntegerValueField(event, MAC_FIELD_KEYCODE)
        if int(kc) not in self._mac_callbacks:
            return False
        rep = Quartz.CGEventGetIntegerValueField(event, MAC_FIELD_AUTOREPEAT)
        pid = Quartz.CGEventGetIntegerValueField(event, MAC_FIELD_SRC_PID)
        return mac_handle(self._held, self._mac_callbacks, int(event_type), int(kc),
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
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
