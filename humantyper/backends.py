"""Keystroke backends: real (pynput) and a fake text-buffer simulator for tests."""
import time
from typing import List, Optional

from . import plat


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

    # ---- selection / clipboard primitives used by the verified locator ----
    def _combo(self, mods, key) -> None:
        for m in mods:
            self._kb.press(m)
        try:
            time.sleep(0.015)
            self._tap(key)
            time.sleep(0.015)
        finally:
            for m in reversed(mods):
                self._kb.release(m)

    def shift_left(self) -> None:
        self._combo([self._Key.shift], self._Key.left)

    def _mod(self, name: str):
        """pynput modifier key by name ("ctrl" / "cmd" / "alt")."""
        return getattr(self._Key, name)

    def select_to_start(self) -> None:
        """Select to start of document (Ctrl+Shift+Home / Cmd+Shift+Up)."""
        if plat.IS_MAC:
            self._combo([self._mod(plat.CMD_KEY), self._Key.shift], self._Key.up)
        else:
            self._combo([self._Key.ctrl, self._Key.shift], self._Key.home)

    def ctrl_left(self) -> None:
        """Word jump (Ctrl/Option+Left)."""
        self._combo([self._mod(plat.WORD_KEY)], self._Key.left)

    def ctrl_shift_left(self) -> None:
        """Select word (Ctrl/Option+Shift+Left)."""
        self._combo([self._mod(plat.WORD_KEY), self._Key.shift], self._Key.left)

    def copy(self) -> None:
        """Copy (Ctrl/Cmd+C)."""
        self._combo([self._mod(plat.CMD_KEY)], "c")

    def clipboard_seq(self) -> int:
        from . import clipboard
        return clipboard.sequence_number()

    def clipboard_get_text(self):
        from . import clipboard
        return clipboard.get_text()

    def clipboard_set_text(self, text: str) -> None:
        from . import clipboard
        clipboard.set_text(text)

    def clipboard_snapshot(self):
        from . import clipboard
        return clipboard.snapshot()

    def clipboard_restore(self, snap) -> None:
        from . import clipboard
        clipboard.restore(snap)

    @property
    def locator(self):
        """Lazily created exact locator: UI Automation (Windows) or Accessibility
        API (macOS). None if the needed library is missing."""
        if not hasattr(self, "_locator"):
            try:
                if plat.IS_MAC:
                    import ApplicationServices  # noqa: F401
                    from .locator_mac import AxLocator
                    self._locator = AxLocator()
                else:
                    import comtypes  # noqa: F401
                    from .locator import UiaLocator
                    self._locator = UiaLocator()
            except Exception:
                self._locator = None
        return self._locator


class FakeLocator:
    """Fake UIA locator over a FakeBackend (same snapshot/select interface as UiaLocator)."""

    def __init__(self, backend: "FakeBackend", available: bool = True, refuse_select: bool = False,
                 crlf: bool = False):
        self.b = backend
        self.available = available
        self.refuse_select = refuse_select
        self.crlf = crlf
        self.calls: List[str] = []

    def snapshot(self):
        self.calls.append("snapshot")
        if not self.available:
            return None
        text = self.b.text
        if self.crlf:
            c = text.replace("\n", "\r\n")
            return c, len(text[:self.b.cursor].replace("\n", "\r\n"))
        return text, self.b.cursor

    def select(self, offset: int, expected_prefix: str) -> bool:
        self.calls.append(f"select:{offset}")
        if not self.available or self.refuse_select:
            return False
        self.b.sel = None
        self.b.cursor = max(0, min(offset, len(self.b.buf)))
        return self.b.text[:self.b.cursor] == expected_prefix


class FakeBackend:
    """Simulates a plain text field with a cursor, a selection, a clipboard and an
    optional autocorrect hook. `key_latency` > 0 makes each key really sleep; the
    default 0 is the zero-sleep mode used by tests.

    autocorrect: callable(fake) invoked after every typed character; it may mutate
    fake.buf / fake.cursor to simulate autocorrect, auto-closing brackets, etc.
    clipboard_works=False simulates a field where Ctrl+C copies nothing."""

    def __init__(self, initial: str = "", key_latency: float = 0.0, autocorrect=None,
                 crlf_clipboard: bool = False, clipboard_works: bool = True,
                 locator_available: Optional[bool] = None, clipboard_initial: str = "",
                 word_style: str = "win", word_jump: bool = True):
        self.buf: List[str] = list(initial)
        self.cursor = len(self.buf)
        self.sel: Optional[int] = None          # selection anchor (cursor is the active end)
        self.key_latency = key_latency
        self.errors: List[str] = []   # impossible actions (backspace at 0, etc.)
        self.keys = 0
        self.autocorrect = autocorrect
        self.crlf_clipboard = crlf_clipboard
        self.clipboard_works = clipboard_works
        self.clipboard = clipboard_initial
        self.clip_seq = 1
        self.clip_restored = 0
        self.word_style = word_style            # "win" | "chrome" | "word" (Ctrl+Left semantics)
        self.word_jump = word_jump              # False: Ctrl+(Shift+)Left does nothing
        self.locator = FakeLocator(self, available=locator_available) if locator_available is not None else None

    @property
    def text(self) -> str:
        return "".join(self.buf)

    def _tick(self):
        self.keys += 1
        if self.key_latency > 0:
            time.sleep(self.key_latency)

    def _delete_selection(self) -> bool:
        if self.sel is None or self.sel == self.cursor:
            self.sel = None
            return False
        a, b = sorted((self.sel, self.cursor))
        del self.buf[a:b]
        self.cursor = a
        self.sel = None
        return True

    def _insert(self, ch: str):
        self._delete_selection()
        self.buf.insert(self.cursor, ch)
        self.cursor += 1
        if self.autocorrect:
            self.autocorrect(self)

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
        if self._delete_selection():
            return
        if self.cursor == 0:
            self.errors.append("backspace at start")
            return
        del self.buf[self.cursor - 1]
        self.cursor -= 1

    def left(self) -> None:
        self._tick()
        if self.sel is not None:
            self.cursor = min(self.sel, self.cursor)
            self.sel = None
            return
        if self.cursor == 0:
            self.errors.append("left at start")
            return
        self.cursor -= 1

    def right(self) -> None:
        self._tick()
        if self.sel is not None:
            self.cursor = max(self.sel, self.cursor)
            self.sel = None
            return
        if self.cursor >= len(self.buf):
            self.errors.append("right at end")
            return
        self.cursor += 1

    def shift_left(self) -> None:
        self._tick()
        if self.sel is None:
            self.sel = self.cursor
        if self.cursor > 0:
            self.cursor -= 1

    def select_to_start(self) -> None:
        self._tick()
        if self.sel is None:
            self.sel = self.cursor
        self.cursor = 0

    def _prev_word(self) -> int:
        """Caret position after Ctrl+Left, per word_style."""
        buf, p = self.buf, self.cursor
        if self.word_style == "chrome":      # whitespace-delimited tokens (punctuation sticks to words)
            while p > 0 and buf[p - 1].isspace():
                p -= 1
            while p > 0 and not buf[p - 1].isspace():
                p -= 1
            return p
        if self.word_style == "word":        # punctuation runs are words of their own
            while p > 0 and buf[p - 1].isspace():
                p -= 1
            if p > 0 and not (buf[p - 1].isalnum() or buf[p - 1] in "_'"):
                while p > 0 and not (buf[p - 1].isalnum() or buf[p - 1] in "_'")                         and not buf[p - 1].isspace():
                    p -= 1
            else:
                while p > 0 and (buf[p - 1].isalnum() or buf[p - 1] in "_'"):
                    p -= 1
            return p
        isw = lambda c: c.isalnum() or c in "_'"      # Windows edit control
        while p > 0 and not isw(buf[p - 1]):
            p -= 1
        while p > 0 and isw(buf[p - 1]):
            p -= 1
        return p

    def ctrl_left(self) -> None:
        self._tick()
        if not self.word_jump:
            return
        self.sel = None                          # jumps from the active end, drops the selection
        self.cursor = self._prev_word()

    def ctrl_shift_left(self) -> None:
        self._tick()
        if not self.word_jump:
            return
        if self.sel is None:
            self.sel = self.cursor
        self.cursor = self._prev_word()

    def copy(self) -> None:
        self._tick()
        if not self.clipboard_works or self.sel is None or self.sel == self.cursor:
            return
        a, b = sorted((self.sel, self.cursor))
        t = "".join(self.buf[a:b])
        self.clipboard = t.replace("\n", "\r\n") if self.crlf_clipboard else t
        self.clip_seq += 1

    def clipboard_seq(self) -> int:
        return self.clip_seq

    def clipboard_get_text(self):
        return self.clipboard

    def clipboard_set_text(self, text: str) -> None:
        self.clipboard = text
        self.clip_seq += 1

    def clipboard_snapshot(self):
        return self.clipboard

    def clipboard_restore(self, snap) -> None:
        self.clipboard = snap
        self.clip_seq += 1
        self.clip_restored += 1
