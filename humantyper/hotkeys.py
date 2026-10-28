"""Global hotkeys (F8 start/stop, F9 pause/resume) that are suppressed system-wide."""
from typing import Callable, Optional

VK_F8 = 0x77
VK_F9 = 0x78
LLKHF_INJECTED = 0x10
_DOWN = (0x100, 0x104)   # WM_KEYDOWN, WM_SYSKEYDOWN
_UP = (0x101, 0x105)     # WM_KEYUP, WM_SYSKEYUP


class GlobalHotkeys:
    def __init__(self, on_f8: Callable[[], None], on_f9: Callable[[], None]):
        self._callbacks = {VK_F8: on_f8, VK_F9: on_f9}
        self._held = set()
        self._listener = None

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

    def start(self) -> None:
        from pynput import keyboard
        self._listener = keyboard.Listener(win32_event_filter=self._filter)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
