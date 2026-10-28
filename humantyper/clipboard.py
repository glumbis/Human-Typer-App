"""Windows clipboard helper (pure ctypes, no pywin32).

Provides sequence number, text read/write and a snapshot/restore of every
memory-backed clipboard format (text, HTML, RTF, files list, DIB, ...). GDI-handle
formats (bitmap, palette, metafiles) are not preserved: Windows re-synthesises
CF_BITMAP from CF_DIB, which is preserved.
"""
import ctypes
import time
from ctypes import wintypes
from typing import List, Optional, Tuple

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
_SKIP = {2, 3, 9, 14}          # CF_BITMAP, METAFILEPICT, PALETTE, ENHMETAFILE (GDI handles)

Snapshot = List[Tuple[int, bytes]]

_u32 = _k32 = None


def _libs():
    global _u32, _k32
    if _u32 is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        u.OpenClipboard.argtypes = [wintypes.HWND]
        u.OpenClipboard.restype = wintypes.BOOL
        u.CloseClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.restype = wintypes.BOOL
        u.GetClipboardData.argtypes = [wintypes.UINT]
        u.GetClipboardData.restype = ctypes.c_void_p
        u.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        u.SetClipboardData.restype = ctypes.c_void_p
        u.EnumClipboardFormats.argtypes = [wintypes.UINT]
        u.EnumClipboardFormats.restype = wintypes.UINT
        u.GetClipboardSequenceNumber.restype = wintypes.DWORD
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k.GlobalAlloc.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        k.GlobalUnlock.restype = wintypes.BOOL
        k.GlobalSize.argtypes = [ctypes.c_void_p]
        k.GlobalSize.restype = ctypes.c_size_t
        k.GlobalFree.argtypes = [ctypes.c_void_p]
        k.GlobalFree.restype = ctypes.c_void_p
        _u32, _k32 = u, k
    return _u32, _k32


def _open(retries: int = 15, delay: float = 0.02) -> bool:
    u, _ = _libs()
    for _ in range(retries):
        if u.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def sequence_number() -> int:
    return int(_libs()[0].GetClipboardSequenceNumber())


def get_text() -> Optional[str]:
    u, k = _libs()
    if not _open():
        return None
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def _put(fmt: int, data: bytes) -> bool:
    u, k = _libs()
    h = k.GlobalAlloc(GMEM_MOVEABLE, len(data) + 2)
    if not h:
        return False
    p = k.GlobalLock(h)
    if not p:
        k.GlobalFree(h)
        return False
    ctypes.memmove(p, data, len(data))
    ctypes.memset(p + len(data), 0, 2)
    k.GlobalUnlock(h)
    if not u.SetClipboardData(fmt, h):
        k.GlobalFree(h)
        return False
    return True


def set_text(text: str) -> bool:
    u, _ = _libs()
    if not _open():
        return False
    try:
        u.EmptyClipboard()
        return _put(CF_UNICODETEXT, text.encode("utf-16-le"))
    finally:
        u.CloseClipboard()


def snapshot() -> Optional[Snapshot]:
    """All memory formats currently on the clipboard; None if it could not be opened."""
    u, k = _libs()
    if not _open():
        return None
    out: Snapshot = []
    try:
        fmt = 0
        while True:
            fmt = u.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _SKIP or 0x80 <= fmt <= 0x8F or 0x300 <= fmt <= 0x3FF:
                continue
            h = u.GetClipboardData(fmt)
            if not h:
                continue
            size = k.GlobalSize(h)
            p = k.GlobalLock(h)
            if not p or not size:
                continue
            try:
                out.append((fmt, ctypes.string_at(p, size)))
            finally:
                k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()
    return out


def restore(snap: Optional[Snapshot]) -> bool:
    if snap is None:
        return False
    u, _ = _libs()
    if not _open():
        return False
    try:
        u.EmptyClipboard()
        ok = True
        for fmt, data in snap:
            ok = _put(fmt, data) and ok
        return ok
    finally:
        u.CloseClipboard()
