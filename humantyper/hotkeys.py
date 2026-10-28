"""Global hotkeys (F8 start/stop, F9 pause/resume) that are suppressed system-wide."""
import os
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

    def _mac_intercept(self, event_type, event):
        """pynput darwin_intercept: return None to suppress, event to pass through."""
        try:
            import Quartz
            kc = Quartz.CGEventGetIntegerValueField(event, MAC_FIELD_KEYCODE)
            rep = Quartz.CGEventGetIntegerValueField(event, MAC_FIELD_AUTOREPEAT)
            pid = Quartz.CGEventGetIntegerValueField(event, MAC_FIELD_SRC_PID)
            if mac_handle(self._held, self._mac_callbacks, int(event_type), int(kc),
                          int(rep), int(pid), os.getpid()):
                return None
        except Exception:
            pass
        return event

    def start(self) -> None:
        """Start the listener. Raises RuntimeError with a readable message on failure
        (also sets .ok / .error)."""
        self.ok, self.error = False, ""
        try:
            from pynput import keyboard
            if plat.IS_MAC:
                self._listener = keyboard.Listener(darwin_intercept=self._mac_intercept)
            else:
                self._listener = keyboard.Listener(win32_event_filter=self._filter)
            self._listener.daemon = True
            self._listener.start()
            if plat.IS_MAC:
                import time
                time.sleep(0.15)
                # pynput's mac listener dies quietly without Input Monitoring permission
                if not self._listener.is_alive():
                    raise RuntimeError("grant Input Monitoring (System Settings > Privacy & Security)")
            self.ok = True
        except Exception as e:
            self.error = str(e) or type(e).__name__
            self._listener = None
            raise RuntimeError(f"Global hotkeys unavailable: {self.error}") from e

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
