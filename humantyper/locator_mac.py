"""macOS Accessibility (AX) locator: the equivalent of UiaLocator.

Same interface (`snapshot`, `select`, `name`). The AX calls live behind a small
"layer" object (`RealAx` via pyobjc, or a fake in tests) so the offset / verify
logic is testable anywhere. Every call runs on one worker thread and is awaited
with a timeout. Returns None / False whenever AX is untrusted or the focused
element does not expose AXValue + AXSelectedTextRange (browsers, Electron,
Google Docs ...), so the engine falls back to clipboard, then counting.

AX offsets are UTF-16 code units; text containing astral characters (emoji) is
declined, because Python indices would differ from AX offsets.
"""
import queue
import threading
from typing import Callable, Optional, Tuple


def has_astral(text: str) -> bool:
    return any(ord(c) > 0xFFFF for c in text)


class AxUnavailable(Exception):
    pass


class RealAx:
    """pyobjc implementation of the AX layer. Import errors surface on first use."""

    def __init__(self):
        import ApplicationServices as AS
        import CoreFoundation as CF
        self.AS, self.CF = AS, CF
        self.system = AS.AXUIElementCreateSystemWide()
        try:
            AS.AXUIElementSetMessagingTimeout(self.system, 1.0)
        except Exception:
            pass

    def trusted(self) -> bool:
        return bool(self.AS.AXIsProcessTrusted())

    def _attr(self, el, name):
        err, val = self.AS.AXUIElementCopyAttributeValue(el, name, None)
        return val if err == 0 else None

    def focused_element(self):
        el = self._attr(self.system, self.AS.kAXFocusedUIElementAttribute)
        if el is None:                       # fall back to the frontmost application
            try:
                from AppKit import NSWorkspace
                app = NSWorkspace.sharedWorkspace().frontmostApplication()
                a = self.AS.AXUIElementCreateApplication(app.processIdentifier())
                self.AS.AXUIElementSetMessagingTimeout(a, 1.0)
                el = self._attr(a, self.AS.kAXFocusedUIElementAttribute)
            except Exception:
                el = None
        if el is not None:
            try:
                self.AS.AXUIElementSetMessagingTimeout(el, 1.0)
            except Exception:
                pass
        return el

    def get_text(self, el) -> Optional[str]:
        v = self._attr(el, self.AS.kAXValueAttribute)
        return str(v) if isinstance(v, str) else None

    def get_caret(self, el) -> Optional[Tuple[int, int]]:
        v = self._attr(el, self.AS.kAXSelectedTextRangeAttribute)
        if v is None:
            return None
        ok, rng = self.AS.AXValueGetValue(v, self.AS.kAXValueCFRangeType, None)
        if not ok:
            return None
        return int(rng.location), int(rng.length)

    def set_caret(self, el, loc: int) -> bool:
        v = self.AS.AXValueCreate(self.AS.kAXValueCFRangeType, self.CF.CFRangeMake(loc, 0))
        if v is None:
            return False
        return self.AS.AXUIElementSetAttributeValue(
            el, self.AS.kAXSelectedTextRangeAttribute, v) == 0


class AxLocator:
    TIMEOUT = 3.0
    name = "AX"

    def __init__(self, ax=None):
        self._ax = ax
        self._q: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._broken = False
        self._lock = threading.Lock()
        self.last_error = ""

    def _ensure(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True, name="ax")
                self._thread.start()

    def _loop(self):
        while True:
            fn, box, ev = self._q.get()
            try:
                box.append(("ok", fn()))
            except BaseException as e:      # noqa
                box.append(("err", e))
            ev.set()

    def _call(self, fn: Callable):
        if self._broken:
            raise AxUnavailable(self.last_error or "AX disabled after timeout")
        self._ensure()
        box: list = []
        ev = threading.Event()
        self._q.put((fn, box, ev))
        if not ev.wait(self.TIMEOUT):
            self._broken = True
            self.last_error = "AX call timed out"
            raise AxUnavailable(self.last_error)
        kind, val = box[0]
        if kind == "err":
            raise val
        return val

    # ---- worker-thread operations ----
    def _layer(self):
        if self._ax is None:
            self._ax = RealAx()
        return self._ax

    def _read(self):
        """(element, text, (loc, len)) of the focused element, or None."""
        ax = self._layer()
        if not ax.trusted():
            self.last_error = "Accessibility permission not granted"
            return None
        el = ax.focused_element()
        if el is None:
            self.last_error = "no focused element"
            return None
        text = ax.get_text(el)
        rng = ax.get_caret(el)
        if text is None or rng is None:
            self.last_error = "no AXValue/AXSelectedTextRange"
            return None
        if has_astral(text):
            self.last_error = "astral characters (UTF-16 offsets)"
            return None
        return el, text, rng

    def _snapshot_impl(self):
        r = self._read()
        if r is None:
            return None
        _el, text, (loc, _length) = r
        if loc < 0 or loc > len(text):
            self.last_error = "caret out of range"
            return None
        return text, loc

    def _select_impl(self, offset: int, expected_prefix: str) -> bool:
        r = self._read()
        if r is None:
            return False
        el, text, _rng = r
        if offset < 0 or offset > len(text):
            return False
        if not self._layer().set_caret(el, offset):
            return False
        r2 = self._read()
        if r2 is None:
            return False
        _el2, text2, (loc2, len2) = r2
        return len2 == 0 and text2[:loc2] == expected_prefix

    # ---- public ----
    def snapshot(self) -> Optional[Tuple[str, int]]:
        try:
            return self._call(self._snapshot_impl)
        except Exception as e:
            self.last_error = str(e) or type(e).__name__
            return None

    def select(self, offset: int, expected_prefix: str) -> bool:
        try:
            return bool(self._call(lambda: self._select_impl(offset, expected_prefix)))
        except Exception as e:
            self.last_error = str(e) or type(e).__name__
            return False
