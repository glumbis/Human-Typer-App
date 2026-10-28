"""Keystroke backends: real (pynput) and a fake text-buffer simulator for tests."""
import time
from typing import List


class PynputBackend:
    """Sends OS-level keystrokes via pynput (Unicode safe)."""

    def __init__(self):
        from pynput.keyboard import Controller, Key  # lazy: keeps tests import-light
        self._kb = Controller()
        self._Key = Key

    def type_char(self, ch: str) -> None:
        self._kb.type(ch)

    def _tap(self, key) -> None:
        self._kb.press(key)
        self._kb.release(key)

    def press_enter(self) -> None:
        self._tap(self._Key.enter)

    def press_tab(self) -> None:
        self._tap(self._Key.tab)

    def backspace(self) -> None:
        self._tap(self._Key.backspace)

    def left(self) -> None:
        self._tap(self._Key.left)

    def right(self) -> None:
        self._tap(self._Key.right)


class FakeBackend:
    """Simulates a plain text field with a cursor. `key_latency` > 0 makes each
    key really sleep; the default 0 is the zero-sleep mode used by tests."""

    def __init__(self, initial: str = "", key_latency: float = 0.0):
        self.buf: List[str] = list(initial)
        self.cursor = len(self.buf)
        self.key_latency = key_latency
        self.errors: List[str] = []   # impossible actions (backspace at 0, etc.)
        self.keys = 0

    @property
    def text(self) -> str:
        return "".join(self.buf)

    def _tick(self):
        self.keys += 1
        if self.key_latency > 0:
            time.sleep(self.key_latency)

    def _insert(self, ch: str):
        self.buf.insert(self.cursor, ch)
        self.cursor += 1

    def type_char(self, ch: str) -> None:
        self._tick()
        self._insert(ch)

    def press_enter(self) -> None:
        self._tick()
        self._insert("\n")

    def press_tab(self) -> None:
        self._tick()
        self._insert("\t")

    def backspace(self) -> None:
        self._tick()
        if self.cursor == 0:
            self.errors.append("backspace at start")
            return
        del self.buf[self.cursor - 1]
        self.cursor -= 1

    def left(self) -> None:
        self._tick()
        if self.cursor == 0:
            self.errors.append("left at start")
            return
        self.cursor -= 1

    def right(self) -> None:
        self._tick()
        if self.cursor >= len(self.buf):
            self.errors.append("right at end")
            return
        self.cursor += 1
