"""Hidden `--e2e <dir>` mode for the macOS end-to-end CI test (tools/mac_e2e.sh).

The app runs normally (nothing is typed by itself); a test helper drives it from outside with real
key presses / mouse clicks. This module only gives the helper a way to configure the app and to see
what happened:

* <dir>/cmd.txt   one JSON object per line, appended by the helper:
      {"cmd": "config", "text": "...", "wpm": 200, "typo": 0, "revise": "end", "locate": "auto"}
      {"cmd": "info"}          -> writes an "info" event (permissions, hotkeys, overlay buttons...)
      {"cmd": "quit"}
* <dir>/e2e.log   one JSON object per line written by the app: state changes, hotkeys received,
  overlay clicks, engine log lines, finish results, errors.
"""
import json
import os
import time
import traceback

from . import plat


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class E2EDriver:
    def __init__(self, app, directory):
        self.app = app
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self.log_path = os.path.join(directory, "e2e.log")
        self.cmd_path = os.path.join(directory, "cmd.txt")
        self._cmd_pos = 0
        self._last = None
        self._wrap()
        self.log("start", pid=os.getpid())
        app.after(300, self._tick)

    # ---- output -------------------------------------------------------
    def log(self, ev, **kw):
        kw.update(t=round(time.time(), 3), ev=ev)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(kw, default=str, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ---- hooks --------------------------------------------------------
    def _wrap(self):
        app = self.app
        orig_handle = app._handle

        def handle(kind, val):
            if kind not in ("char", "progress"):
                self.log("event", kind=kind, val=val)
            eng = app.engine
            try:
                return orig_handle(kind, val)
            finally:
                if kind == "finished" and getattr(eng, "error", None):
                    self.log("engine_error", tb=eng.error)
        app._handle = handle

        ov = app.overlay
        if ov is not None:
            orig_fire = ov._fire

            def fire(name):
                self.log("overlay_click", button=name, st=app.st)
                return orig_fire(name)
            ov._fire = fire

        orig_report = app.report_callback_exception

        def report(exc, val, tb):
            self.log("tk_error", tb="".join(traceback.format_exception(exc, val, tb)))
            return orig_report(exc, val, tb)
        app.report_callback_exception = report

    # ---- polling ------------------------------------------------------
    def _tick(self):
        try:
            self._read_cmds()
            app = self.app
            paused = bool(app.engine and app.engine.paused)
            cur = (app.st, paused, app._overlay_state())
            if cur != self._last:
                self._last = cur
                self.log("state", st=app.st, paused=paused, overlay=cur[2], status=app._status_text)
        except Exception:
            self.log("e2e_error", tb=traceback.format_exc())
        self.app.after(100, self._tick)

    def _read_cmds(self):
        try:
            with open(self.cmd_path, "r", encoding="utf-8") as f:
                f.seek(self._cmd_pos)
                data = f.read()
        except FileNotFoundError:
            return
        if not data.endswith("\n"):
            data = data[:data.rfind("\n") + 1]
        self._cmd_pos += len(data.encode("utf-8"))
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self._do(json.loads(line))
            except Exception:
                self.log("cmd_error", line=line, tb=traceback.format_exc())

    def _do(self, c):
        app = self.app
        cmd = c.get("cmd")
        if cmd == "config":
            if "text" in c:
                app._use_ai_text(c["text"])
            if "wpm" in c:
                app.wpm_var.set(_num(c["wpm"], 60))
                app._wpm_text(app.wpm_var.get())
            if "typo" in c:
                app.typo_var.set(_num(c["typo"], 0))
                app._typo_text(app.typo_var.get())
            if "revise" in c:
                app.mode_btn.set(c["revise"].upper())
            if "locate" in c:
                app.locate_btn.set(c["locate"])
            if c.get("overlay") is not None:
                app._set_overlay(bool(c["overlay"]))
            self.log("configured", text=app._text(), settings=repr(app._settings()))
        elif cmd == "info":
            self.log("info", **info(app))
        elif cmd == "setup":
            app.open_setup()
            self.log("setup_opened", shown=setup_info(app))
        elif cmd == "axprobe":
            self.log("axprobe", **ax_probe())
        elif cmd == "quit":
            self.log("quit")
            app.after(50, app._on_close)
        else:
            self.log("cmd_unknown", cmd=cmd)


def _button(w):
    try:
        w.update_idletasks()
        x, y = w.winfo_rootx(), w.winfo_rooty()
        ww, hh = w.winfo_width(), w.winfo_height()
        return {"x": x, "y": y, "w": ww, "h": hh, "cx": x + ww // 2, "cy": y + hh // 2,
                "viewable": bool(w.winfo_viewable()), "enabled": getattr(w, "enabled", None)}
    except Exception as e:
        return {"error": repr(e)}


def mac_permissions():
    """AXIsProcessTrusted / CGPreflightListenEventAccess / CGPreflightPostEventAccess (None = unknown)."""
    d = {"ax_trusted": None, "listen_access": None, "post_access": None}
    if not plat.IS_MAC:
        return d
    from .ui import macutil
    d["ax_trusted"] = macutil.ax_trusted()
    try:
        import Quartz
        d["listen_access"] = bool(Quartz.CGPreflightListenEventAccess())
        d["post_access"] = bool(Quartz.CGPreflightPostEventAccess())
    except Exception as e:
        d["preflight_error"] = repr(e)
    return d


def _frontmost():
    try:
        from AppKit import NSWorkspace
        a = NSWorkspace.sharedWorkspace().frontmostApplication()
        return {"pid": int(a.processIdentifier()), "bundle": str(a.bundleIdentifier())}
    except Exception as e:
        return {"error": repr(e)}


def info(app):
    d = {"pid": os.getpid(), "st": app.st, "status": app._status_text}
    d.update(mac_permissions())
    hk = app.hotkeys
    d["hotkeys"] = {"ok": hk.ok, "error": hk.error, "mode": getattr(hk, "mode", None)}
    tap = getattr(hk, "_listener", None)
    if tap is not None:
        d["hotkeys"]["tap_events"] = getattr(tap, "events_seen", None)
        d["hotkeys"]["tap_last"] = getattr(tap, "last_event", None)
    d["frontmost"] = _frontmost()
    d["backend"] = type(app.backend).__name__ if app.backend is not None else None
    ov = app.overlay
    if ov is not None:
        d["overlay"] = {"shown": ov.shown, "geometry": ov.geometry(),
                        "focus_return": ov.can_return_focus(),
                        "focus_last": _app_desc(getattr(ov.focus_ret, "last", None) if ov.focus_ret else None),
                        "buttons": {n: _button(getattr(ov, "b_" + n)) for n in
                                    ("start", "pause", "stop", "home", "close")}}
    d["setup"] = setup_info(app)
    d["main_buttons"] = {"start": _canvas_btn(app, app.start_btn), "stop": _canvas_btn(app, app.stop_btn)}
    return d


def _canvas_btn(app, g):
    try:
        b = g.bbox()
        cv = app._cv
        x0, y0 = cv.winfo_rootx(), cv.winfo_rooty()
        return {"cx": x0 + (b[0] + b[2]) // 2, "cy": y0 + (b[1] + b[3]) // 2, "bbox": b}
    except Exception as e:
        return {"error": repr(e)}


def _app_desc(a):
    if a is None:
        return None
    try:
        return {"pid": int(a.processIdentifier()), "bundle": str(a.bundleIdentifier())}
    except Exception as e:
        return repr(e)


def ax_probe():
    """Step-by-step Accessibility read of the focused element (what AxLocator relies on)."""
    d = {}
    try:
        import ApplicationServices as AS
        sysw = AS.AXUIElementCreateSystemWide()
        err, el = AS.AXUIElementCopyAttributeValue(sysw, AS.kAXFocusedUIElementAttribute, None)
        d["focused"] = (err, repr(el)[:160])
        if el is not None:
            for name in ("AXRole", "AXValue", "AXSelectedTextRange"):
                e2, v = AS.AXUIElementCopyAttributeValue(el, name, None)
                d[name] = (e2, type(v).__name__, repr(v)[:160])
            e2, v = AS.AXUIElementCopyAttributeValue(el, "AXSelectedTextRange", None)
            try:
                d["range_decoded"] = repr(AS.AXValueGetValue(v, AS.kAXValueCFRangeType, None))
            except Exception as e:
                d["range_decode_error"] = repr(e)
    except Exception:
        d["error"] = traceback.format_exc()
    try:
        from .locator_mac import AxLocator
        loc = AxLocator()
        d["locator_snapshot"] = repr(loc.snapshot())[:200]
        d["locator_error"] = loc.last_error
    except Exception:
        d["locator_exc"] = traceback.format_exc()
    return d


def setup_info(app):
    """SETUP window: None when closed, else its report() (state, per-permission status...)."""
    w = getattr(app, "_setup", None)
    try:
        if w is None or not w.winfo_exists():
            return None
        r = w.report()
        r["geometry"] = w.geometry()
        return r
    except Exception as e:
        return {"error": repr(e)}
