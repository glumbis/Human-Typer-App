"""macOS helpers (all guarded; importing this on Windows/Linux is harmless).

* permission checks (Accessibility / Input Monitoring) and deep links to System Settings
* FocusReturn: remember the last frontmost app that is not us and re-activate it
* floating, non-activating panel style for the stats overlay (Aqua Tk)
* visible screen area (menu bar and Dock excluded) via AppKit
* window icon via iconphoto
"""
import os
import subprocess
import tkinter as tk

from .. import plat

AX_LIB = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
PANE_ACCESSIBILITY = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
PANE_INPUT_MONITORING = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
ACTIVATE_IGNORING_OTHER_APPS = 1 << 1      # NSApplicationActivateIgnoringOtherApps

PERM_TEXT = ("macOS needs permission: System Settings > Privacy & Security > Accessibility > "
             "enable Human Typer (and Input Monitoring for F8/F9).")
INPUT_MON_TEXT = ("F8/F9 hotkeys need Input Monitoring: System Settings > Privacy & Security > "
                  "Input Monitoring > enable Human Typer, then restart the app.")


def hotkey_labels():
    """(start/stop label, pause label) as printed on screen."""
    return ("fn+F8", "fn+F9") if plat.IS_MAC else ("F8", "F9")


# ---- permissions ---------------------------------------------------------------
def ax_trusted(prompt=False):
    """True/False if the process may control the computer (Accessibility); None when unknown/not mac."""
    if not plat.IS_MAC:
        return None
    try:
        if prompt:
            try:
                from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
                return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
            except Exception:
                pass
        import ctypes
        lib = ctypes.cdll.LoadLibrary(AX_LIB)
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        lib.AXIsProcessTrusted.argtypes = []
        return bool(lib.AXIsProcessTrusted())
    except Exception:
        return None


def open_settings(pane=PANE_ACCESSIBILITY, runner=subprocess.run):
    """Open a Privacy & Security pane. Returns True if the `open` command was launched."""
    try:
        runner(["open", pane], check=False)
        return True
    except Exception:
        return False


# ---- focus return --------------------------------------------------------------
def _default_workspace():
    try:
        from AppKit import NSWorkspace
        return NSWorkspace.sharedWorkspace()
    except Exception:
        return None


class FocusReturn:
    """Remembers the last frontmost app that is not this process; restore() re-activates it.

    `workspace` is anything with frontmostApplication() whose result has processIdentifier() and
    activateWithOptions_(opts) (NSWorkspace/NSRunningApplication); injectable for tests."""

    def __init__(self, workspace=None, pid=None):
        self.ws = workspace if workspace is not None else _default_workspace()
        self.pid = os.getpid() if pid is None else pid
        self.last = None

    @property
    def available(self):
        return self.ws is not None

    def _front(self):
        try:
            return self.ws.frontmostApplication()
        except Exception:
            return None

    def poll(self):
        """Call periodically; records the frontmost app if it is not us."""
        if self.ws is None:
            return None
        app = self._front()
        try:
            if app is not None and int(app.processIdentifier()) != self.pid:
                self.last = app
        except Exception:
            pass
        return self.last

    def restore(self):
        """Re-activate the remembered app (no-op if focus is already on another app). True if we tried."""
        app = self.last
        if self.ws is None or app is None:
            return False
        try:
            cur = self._front()
            if cur is not None and int(cur.processIdentifier()) != self.pid:
                return False
            app.activateWithOptions_(ACTIVATE_IGNORING_OTHER_APPS)
            return True
        except Exception:
            return False


# ---- overlay window style ------------------------------------------------------
def make_floating_panel(widget):
    """Turn a (still withdrawn) Tk toplevel into a floating, title-less, non-activating Aqua panel.
    Returns True when Tk accepted the style (so overrideredirect is not needed)."""
    if not plat.IS_MAC:
        return False
    try:
        widget.tk.call("::tk::unsupported::MacWindowStyle", "style", widget._w,
                       "floating", ("noTitleBar", "noActivates"))
        return True
    except Exception:
        return False


# ---- screen area ---------------------------------------------------------------
def visible_area(screen_w, screen_h, screen=None, menu_bar=25, dock=80):
    """(x, y, w, h) of the usable screen area with y measured from the top.

    `screen` may be an NSScreen-like object (frame()/visibleFrame(); Cocoa's y origin is bottom-left).
    Falls back to the Tk screen size minus an assumed menu bar and Dock."""
    try:
        if screen is None:
            from AppKit import NSScreen
            screen = NSScreen.mainScreen()
        fr, vf = screen.frame(), screen.visibleFrame()
        x = vf.origin.x - fr.origin.x
        y_top = (fr.origin.y + fr.size.height) - (vf.origin.y + vf.size.height)
        w, h = vf.size.width, vf.size.height
        if w > 0 and h > 0:
            return int(x), int(y_top), int(w), int(h)
    except Exception:
        pass
    return 0, menu_bar, int(screen_w), int(screen_h) - menu_bar - dock


# ---- icon ----------------------------------------------------------------------
def set_icon(win, png_path, default=True):
    """Set the window icon from a PNG (the Dock icon of the .app comes from its .icns)."""
    if not os.path.exists(png_path):
        return False
    try:
        img = tk.PhotoImage(file=png_path)
        win.iconphoto(default, img)
        win._icon_ref = img                      # keep a reference or Tk drops it
        return True
    except Exception:
        return False
