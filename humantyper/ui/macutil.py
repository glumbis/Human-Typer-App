"""macOS helpers (all guarded; importing this on Windows/Linux is harmless).

* permission checks (Accessibility / Input Monitoring) and deep links to System Settings
* FocusReturn: remember the last frontmost app that is not us and re-activate it
* App Nap opt-out
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
             "enable Human Typer (and Input Monitoring for F8/F9). Already on after an update? "
             "Remove it with - and add it again with +.")
INPUT_MON_TEXT = ("F8/F9 hotkeys need Input Monitoring (or Accessibility): System Settings > Privacy & "
                  "Security > Input Monitoring > enable Human Typer. They switch on by themselves.")


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


def _quartz():
    try:
        import Quartz
        return Quartz
    except Exception:
        return None


def _q_call(quartz, name):
    """Call an optional Quartz function (macOS 10.15+, pyobjc 6+). None if missing/failing."""
    q = quartz if quartz is not None else _quartz()
    fn = getattr(q, name, None) if q is not None else None
    if fn is None:
        return None
    try:
        return bool(fn())
    except Exception:
        return None


def listen_access(quartz=None):
    """Input Monitoring granted? (CGPreflightListenEventAccess; None = unknown / not a Mac)."""
    if quartz is None and not plat.IS_MAC:
        return None
    return _q_call(quartz, "CGPreflightListenEventAccess")


def post_access(quartz=None):
    """May we post key events? (CGPreflightPostEventAccess, part of Accessibility in Settings)."""
    if quartz is None and not plat.IS_MAC:
        return None
    return _q_call(quartz, "CGPreflightPostEventAccess")


def request_accessibility(quartz=None, ax_prompt=None):
    """Ask macOS to show its own Accessibility prompt; it also adds Human Typer to the list, so no
    "+" is needed. Returns the current trust state (True/False/None)."""
    if quartz is None and ax_prompt is None and not plat.IS_MAC:
        return None
    _q_call(quartz, "CGRequestPostEventAccess")
    return (ax_prompt or (lambda: ax_trusted(prompt=True)))()


def request_input_monitoring(quartz=None):
    """Ask macOS for Input Monitoring (system prompt + adds the app to the list)."""
    if quartz is None and not plat.IS_MAC:
        return None
    return _q_call(quartz, "CGRequestListenEventAccess")


# one row per permission in the SETUP window: (key, title, what for, Settings pane)
PERMISSIONS = (
    ("accessibility", "1) ACCESSIBILITY", "lets Human Typer press keys", PANE_ACCESSIBILITY),
    ("input", "2) INPUT MONITORING", "lets fn+F8 / fn+F9 work", PANE_INPUT_MONITORING),
)


def permission_status(ax=None, listen=None):
    """{"accessibility": bool|None, "input": bool|None} as macOS reports it right now."""
    return {"accessibility": ax_trusted() if ax is None else ax(),
            "input": listen_access() if listen is None else listen()}


def setup_state(status, hotkeys_ok, tap_failures=0, restart_after=3):
    """Pure decision for the SETUP window:
    "missing"  - a permission is not granted yet (unknown counts as granted: never nag on guesses),
    "restart"  - all granted, but the hotkey tap still cannot be created after `restart_after`
                 tries (macOS sometimes only applies Input Monitoring after a relaunch),
    "waiting"  - all granted, hotkeys not on yet (the next retry will probably succeed),
    "ok"       - everything works."""
    if any(v is False for v in status.values()):
        return "missing"
    if hotkeys_ok:
        return "ok"
    return "restart" if tap_failures >= restart_after else "waiting"


def bundle_path(executable):
    """/Applications/HumanTyper.app for .../HumanTyper.app/Contents/MacOS/HumanTyper, else None."""
    parts = executable.split("/")
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].endswith(".app") and parts[i + 1:i + 2] == ["Contents"]:
            return "/".join(parts[:i + 1])
    return None


def relaunch_command(executable, argv, frozen):
    """Command that starts a fresh copy of the app: `open -n <bundle>` for the .app (so macOS treats
    it like a Finder launch and re-reads the permissions), else the same interpreter + script."""
    app = bundle_path(executable) if frozen else None
    if app:
        return ["open", "-n", app]
    return [executable] + list(argv)


# ---- App Nap -------------------------------------------------------------------
NS_ACTIVITY_USER_INITIATED_ALLOWING_IDLE_SLEEP = 0x00FFFFFF & ~(1 << 20)


def disable_app_nap(process_info=None, reason="Human Typer types into other apps on a hotkey"):
    """Opt out of App Nap: while our window is hidden behind the target app macOS would otherwise
    throttle the Tk timers (hotkeys handled seconds late) and the typing thread's sleeps.
    Returns the activity token (keep a reference) or None."""
    if not plat.IS_MAC and process_info is None:
        return None
    try:
        if process_info is None:
            from Foundation import NSProcessInfo
            process_info = NSProcessInfo.processInfo()
        return process_info.beginActivityWithOptions_reason_(
            NS_ACTIVITY_USER_INITIATED_ALLOWING_IDLE_SLEEP, reason)
    except Exception:
        return None


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

    def self_front(self):
        """True while this process is the frontmost app (None if unknown)."""
        if self.ws is None:
            return None
        app = self._front()
        try:
            return app is not None and int(app.processIdentifier()) == self.pid
        except Exception:
            return None

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
