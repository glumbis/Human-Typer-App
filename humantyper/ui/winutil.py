"""Win32 helpers: make a Tk toplevel a non-activating, always-on-top tool window."""
import sys

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x10, 0x20
HWND_TOPMOST = -1
WANT = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST


def _user32():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.GetParent.restype = wintypes.HWND
    u.GetParent.argtypes = [wintypes.HWND]
    u.GetWindowLongW.restype = ctypes.c_long
    u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetWindowLongW.restype = ctypes.c_long
    u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.GetForegroundWindow.restype = wintypes.HWND
    return u


def _signed(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def top_hwnd(widget):
    """The real top-level HWND of a Tk toplevel (the wrapper that owns the client window)."""
    u = _user32()
    if u is None:
        return 0
    child = widget.winfo_id()
    return u.GetParent(child) or child


def get_exstyle(hwnd) -> int:
    u = _user32()
    return (u.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF) if u and hwnd else 0


def make_noactivate(widget) -> int:
    """Apply NOACTIVATE|TOOLWINDOW|TOPMOST; returns the ex-style read back from the window."""
    u = _user32()
    if u is None:
        return 0
    hwnd = top_hwnd(widget)
    if not hwnd:
        return 0
    cur = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    u.SetWindowLongW(hwnd, GWL_EXSTYLE, _signed(cur | WANT))
    u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                   SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    return get_exstyle(hwnd)


def raise_topmost(widget) -> None:
    """Re-assert topmost without activating (other topmost windows may have overtaken us)."""
    u = _user32()
    hwnd = top_hwnd(widget) if u else 0
    if hwnd:
        u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def foreground_hwnd() -> int:
    u = _user32()
    return int(u.GetForegroundWindow() or 0) if u else 0
