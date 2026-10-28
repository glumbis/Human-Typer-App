"""macOS clipboard (NSPasteboard via pyobjc, with a pbpaste/pbcopy fallback).

Same public API as the Windows module: sequence_number, get_text, set_text,
snapshot, restore. All classes take an injectable pasteboard so the logic is
unit-tested on any OS.

Sequence number: NSPasteboard.changeCount(). In the subprocess fallback there is
no change counter, so it is a hash of the current text plus a counter bumped on our
own writes; waiting for a change therefore compares *content* (copying text that
is identical to what is already on the clipboard is not detected there).
"""
import subprocess
import zlib
from typing import List, Optional, Tuple

Snapshot = List[List[Tuple[str, bytes]]]      # per item: [(type, data), ...]


class NSPasteboardBackend:
    """Wraps an NSPasteboard-like object (real one or a test fake)."""

    def __init__(self, pb=None, string_type=None, item_cls=None, nsdata=None):
        if pb is None:
            from AppKit import NSPasteboard, NSPasteboardTypeString, NSPasteboardItem
            from Foundation import NSData
            pb = NSPasteboard.generalPasteboard()
            string_type = NSPasteboardTypeString
            item_cls = NSPasteboardItem
            nsdata = NSData
        self.pb = pb
        self.string_type = string_type or "public.utf8-plain-text"
        self.item_cls = item_cls
        self.nsdata = nsdata

    def sequence_number(self) -> int:
        return int(self.pb.changeCount())

    def get_text(self) -> Optional[str]:
        s = self.pb.stringForType_(self.string_type)
        return None if s is None else str(s)

    def set_text(self, text: str) -> bool:
        self.pb.clearContents()
        return bool(self.pb.setString_forType_(text, self.string_type))

    def snapshot(self) -> Optional[Snapshot]:
        try:
            items = self.pb.pasteboardItems() or []
            out: Snapshot = []
            for it in items:
                row = []
                for t in (it.types() or []):
                    d = it.dataForType_(t)
                    if d is not None:
                        row.append((str(t), bytes(d)))
                out.append(row)
            return out
        except Exception:
            t = self.get_text()
            return [[(self.string_type, t.encode("utf-8"))]] if t is not None else []

    def restore(self, snap: Optional[Snapshot]) -> bool:
        if snap is None:
            return False
        try:
            self.pb.clearContents()
            if not snap:
                return True
            if self.item_cls is None or self.nsdata is None:
                raise RuntimeError("no item class")
            objs = []
            for row in snap:
                it = self.item_cls.alloc().init() if hasattr(self.item_cls, "alloc") else self.item_cls()
                for t, data in row:
                    it.setData_forType_(self.nsdata.dataWithBytes_length_(data, len(data)), t)
                objs.append(it)
            return bool(self.pb.writeObjects_(objs))
        except Exception:
            # fallback: text only
            try:
                for row in snap:
                    for t, data in row:
                        if t == self.string_type:
                            self.pb.clearContents()
                            return bool(self.pb.setString_forType_(data.decode("utf-8", "replace"),
                                                                    self.string_type))
            except Exception:
                pass
            return False


class SubprocessBackend:
    """pbpaste/pbcopy fallback (text only)."""

    def __init__(self, run=None):
        self._run = run or subprocess.run
        self._writes = 0
        self._last: Optional[str] = None

    def _paste(self) -> Optional[str]:
        try:
            r = self._run(["pbpaste"], capture_output=True, timeout=2)
            return r.stdout.decode("utf-8", "replace")
        except Exception:
            return None

    def sequence_number(self) -> int:
        t = self._paste() or ""
        return (zlib.crc32(t.encode("utf-8")) + self._writes) & 0xFFFFFFFF

    def get_text(self) -> Optional[str]:
        t = self._paste()
        return t if t else None

    def set_text(self, text: str) -> bool:
        try:
            self._run(["pbcopy"], input=text.encode("utf-8"), timeout=2, check=True)
            self._writes += 1
            return True
        except Exception:
            return False

    def snapshot(self):
        return self._paste()

    def restore(self, snap) -> bool:
        if snap is None:
            return False
        return self.set_text(snap)


_impl = None


def _get():
    global _impl
    if _impl is None:
        try:
            _impl = NSPasteboardBackend()
        except Exception:
            _impl = SubprocessBackend()
    return _impl


def sequence_number() -> int:
    return _get().sequence_number()


def get_text() -> Optional[str]:
    return _get().get_text()


def set_text(text: str) -> bool:
    return _get().set_text(text)


def snapshot():
    return _get().snapshot()


def restore(snap) -> bool:
    return _get().restore(snap)
