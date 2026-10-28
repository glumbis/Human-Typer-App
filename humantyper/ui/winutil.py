"""Win32 helpers: make a Tk toplevel a non-activating, always-on-top tool window."""
import sys

from .. import plat
from . import macutil

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


def fit_to_workarea(win, w, h, min_w, min_h, scaling=1.0, x_frac=0.5):
    """Size a (customtkinter) window to at most w x h logical px so it sits fully inside the
    monitor work area (small, high-DPI laptop screens cut the bottom off otherwise)."""
    s = scaling or 1.0
    if plat.IS_MAC:
        area = macutil.visible_area(win.winfo_screenwidth(), win.winfo_screenheight())
        wx, wy, ww, wh = area[0] / s, area[1] / s, area[2] / s, area[3] / s
    elif sys.platform != "win32":
        return
    else:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):   # SPI_GETWORKAREA
            return
        wx, wy = r.left / s, r.top / s
        ww, wh = (r.right - r.left) / s, (r.bottom - r.top) / s
    w, h = min(w, int(ww) - 16), min(h, int(wh) - 40)            # 40 = title bar + frame
    win.minsize(min(min_w, w), min(min_h, h))
    win.geometry(f"{w}x{h}+{int(wx + (ww - w) * x_frac)}+{int(wy)}")
