"""Locating the revision target inside the real document.

* Pure helpers (`choose_occurrence`, `normalize`) are shared by the UI Automation
  (A) and clipboard (B) strategies and are unit-tested without any real window.
* `UiaLocator` is the real Windows UI Automation implementation (comtypes). It
  exposes a tiny interface (`snapshot`, `select`) so the engine logic can be
  tested with a fake locator.

All UIA/COM calls run on one dedicated worker thread (own CoInitialize) and are
awaited with a timeout, so a hung target application can never freeze typing.
"""
import queue
import sys
import threading
from typing import Callable, List, Optional, Tuple


def normalize(s: str) -> str:
    """Clipboard/UIA newline forms -> single "\\n" (one caret step)."""
    return s.replace("\r\n", "\n").replace("\r", "\n")


def find_all(text: str, needle: str) -> List[int]:
    """End offsets of every (possibly overlapping) occurrence of needle in text."""
    ends, i = [], text.find(needle)
    while i != -1:
        ends.append(i + len(needle))
        i = text.find(needle, i + 1)
    return ends


def _suffix_match(a: str, b: str, cap: int = 24) -> int:
    n = 0
    while n < cap and n < len(a) and n < len(b) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n


def choose_occurrence(text_before: str, anchor: str, expected_dist: int,
                      ctx_before: str = "") -> Optional[int]:
    """End offset of the occurrence of `anchor` in `text_before` (all text before
    the caret) that best fits: first the longest match between the text preceding
    the occurrence and the engine's own preceding text `ctx_before`, then the
    distance from the caret closest to `expected_dist`. None if absent or if two
    different occurrences tie on both criteria (ambiguous)."""
    if not anchor:
        return 0 if expected_dist >= 0 else None
    ends = find_all(text_before, anchor)
    if not ends:
        return None
    n = len(text_before)
    scored = sorted((-_suffix_match(text_before[:e - len(anchor)], ctx_before),
                     abs((n - e) - expected_dist), e) for e in ends)
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        return None
    return scored[0][2]


class UiaUnavailable(Exception):
    pass


class UiaLocator:
    """Real UIA locator. Interface used by the engine:
    snapshot() -> (text, caret_offset) | None, select(offset, expected_prefix) -> bool."""

    TIMEOUT = 3.0

    def __init__(self):
        self._q: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._broken = False
        self._lock = threading.Lock()
        self._doc = None          # (range of whole doc) from the last snapshot, worker-thread only
        self.last_error = ""

    # ---- worker thread plumbing ------------------------------------------
    def _ensure(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True, name="uia")
                self._thread.start()

    def _loop(self):
        try:
            import comtypes
            import comtypes.client
            if getattr(sys, "frozen", False):
                comtypes.client.gen_dir = None      # generate wrappers in memory (frozen exe)
            comtypes.CoInitialize()
        except Exception as e:                      # pragma: no cover
            self.last_error = f"comtypes: {e}"
            self._broken = True
            while True:
                fn, box, ev = self._q.get()
                box.append(("err", UiaUnavailable(self.last_error)))
                ev.set()
        self._uia = None
        while True:
            fn, box, ev = self._q.get()
            try:
                box.append(("ok", fn()))
            except BaseException as e:              # noqa
                box.append(("err", e))
            ev.set()

    def _call(self, fn: Callable):
        if self._broken:
            raise UiaUnavailable(self.last_error or "UIA disabled after timeout")
        self._ensure()
        box: list = []
        ev = threading.Event()
        self._q.put((fn, box, ev))
        if not ev.wait(self.TIMEOUT):
            self._broken = True
            self.last_error = "UIA call timed out"
            raise UiaUnavailable(self.last_error)
        kind, val = box[0]
        if kind == "err":
            raise val
        return val

    # ---- UIA operations (run on the worker thread) -------------------------
    def _get_uia(self):
        if self._uia is None:
            import comtypes.client
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as UIA
            self._UIA = UIA
            self._uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=UIA.IUIAutomation)
        return self._uia

    def _pattern(self, el):
        UIA = self._UIA
        p2 = p1 = None
        try:
            raw = el.GetCurrentPattern(UIA.UIA_TextPattern2Id)
            if raw:
                p2 = raw.QueryInterface(UIA.IUIAutomationTextPattern2)
        except Exception:
            p2 = None
        try:
            raw = el.GetCurrentPattern(UIA.UIA_TextPatternId)
            if raw:
                p1 = raw.QueryInterface(UIA.IUIAutomationTextPattern)
        except Exception:
            p1 = None
        return p1, p2

    def _caret_range(self, p1, p2):
        if p2 is not None:
            try:
                _active, rng = p2.GetCaretRange()
                if rng:
                    return rng
            except Exception:
                pass
        sel = p1.GetSelection()
        if sel is not None and sel.Length > 0:
            return sel.GetElement(0)
        return None

    def _snapshot_impl(self):
        uia = self._get_uia()
        UIA = self._UIA
        el = None
        for _ in range(3):          # Chrome/Edge activate accessibility on first query
            el = uia.GetFocusedElement()
            if el is not None:
                p1, p2 = self._pattern(el)
                if p1 is not None:
                    break
            import time
            time.sleep(0.25)
        else:
            self.last_error = "no TextPattern"
            return None
        doc = p1.DocumentRange
        caret = self._caret_range(p1, p2)
        if caret is None:
            self.last_error = "no caret"
            return None
        text = doc.GetText(-1)
        pre = doc.Clone()
        pre.MoveEndpointByRange(UIA.TextPatternRangeEndpoint_End, caret,
                                UIA.TextPatternRangeEndpoint_Start)
        off = len(pre.GetText(-1))
        self._doc = (p1, doc)
        return text, off

    def _prefix_impl(self) -> Optional[str]:
        """Text before the current caret (re-read live)."""
        UIA = self._UIA
        el = self._get_uia().GetFocusedElement()
        p1, p2 = self._pattern(el)
        if p1 is None:
            return None
        caret = self._caret_range(p1, p2)
        if caret is None:
            return None
        pre = p1.DocumentRange.Clone()
        pre.MoveEndpointByRange(UIA.TextPatternRangeEndpoint_End, caret,
                                UIA.TextPatternRangeEndpoint_Start)
        return pre.GetText(-1)

    def _select_impl(self, offset: int, expected_prefix: str) -> bool:
        UIA = self._UIA
        el = self._get_uia().GetFocusedElement()
        p1, _p2 = self._pattern(el)
        if p1 is None:
            return False
        doc = p1.DocumentRange
        r = doc.Clone()
        # collapse to doc start, then extend End by `offset` characters, then collapse to End
        r.MoveEndpointByRange(UIA.TextPatternRangeEndpoint_End, doc, UIA.TextPatternRangeEndpoint_Start)
        if offset:
            r.MoveEndpointByUnit(UIA.TextPatternRangeEndpoint_End, UIA.TextUnit_Character, offset)
        r.MoveEndpointByRange(UIA.TextPatternRangeEndpoint_Start, r, UIA.TextPatternRangeEndpoint_End)
        r.Select()
        return self._prefix_impl() == expected_prefix

    # ---- public interface --------------------------------------------------
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
