#!/usr/bin/env python3
"""macOS end-to-end test driver for Human Typer (run by tools/mac_e2e.sh, CI only).

Launches the built HumanTyper.app with the hidden `--e2e <dir>` flag, puts TextEdit in front and then
behaves like a user: presses F8 / fn+F8 / F9 as real HID key events (CGEventPost from THIS process)
and clicks the floating overlay's buttons with real mouse events. After each case it reads the
TextEdit document through the Accessibility API and compares it with the expected text.

Needs pyobjc (Quartz, ApplicationServices, AppKit) and must itself be allowed to post events / use
Accessibility (GitHub's macOS runners allow that for processes started from the job's shell).
Exit code 0 = every case passed. Results: <dir>/results.json, per-case TextEdit text and screenshots.
"""
import argparse
import json
import os
import subprocess
import sys
import time

import ApplicationServices as AS
import Quartz as Q

KC_F8, KC_F9 = 100, 101
TEXTEDIT = "com.apple.TextEdit"
APP_ID = "io.github.glumbis.humantyper"
SOURCE = "Hello from Human Typer on macOS. This is [quick][a real] test."
EXPECTED = "Hello from Human Typer on macOS. This is a real test."
LONG = ("The quick brown fox jumps over the lazy dog while the slow typist keeps on going. " * 6).strip()


# ---------------------------------------------------------------- low level input
def post_key(keycode, fn=False, hold=0.08):
    for down in (True, False):
        ev = Q.CGEventCreateKeyboardEvent(None, keycode, down)
        Q.CGEventSetFlags(ev, Q.kCGEventFlagMaskSecondaryFn if fn else 0)
        Q.CGEventPost(Q.kCGHIDEventTap, ev)
        time.sleep(hold)


def post_combo(keycode, flags):
    for down in (True, False):
        ev = Q.CGEventCreateKeyboardEvent(None, keycode, down)
        Q.CGEventSetFlags(ev, flags)
        Q.CGEventPost(Q.kCGHIDEventTap, ev)
        time.sleep(0.05)


def click(x, y):
    pt = Q.CGPointMake(float(x), float(y))
    mv = Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, pt, Q.kCGMouseButtonLeft)
    Q.CGEventPost(Q.kCGHIDEventTap, mv)
    time.sleep(0.35)                     # let the window see <Enter> (hover) like a real pointer
    for et in (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp):
        ev = Q.CGEventCreateMouseEvent(None, et, pt, Q.kCGMouseButtonLeft)
        Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, 1)
        Q.CGEventPost(Q.kCGHIDEventTap, ev)
        time.sleep(0.09)


# ---------------------------------------------------------------- apps
def sh(*cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"<{e}>"


def frontmost():
    """Bundle id of the app that has the keyboard focus (asked through Accessibility)."""
    try:
        from AppKit import NSRunningApplication
        err, app = AS.AXUIElementCopyAttributeValue(AS.AXUIElementCreateSystemWide(), "AXFocusedApplication", None)
        if err != 0 or app is None:
            return f"?ax{err}"
        err, pid = AS.AXUIElementGetPid(app, None)
        ra = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        return str(ra.bundleIdentifier()) if ra is not None else f"?pid{pid}"
    except Exception as e:
        return f"?{e!r}"


def pid_of(bundle):
    out = sh("lsappinfo", "info", "-only", "pid", "-app", bundle)
    try:
        return int(out.split("=")[-1].strip())
    except ValueError:
        return None


def activate(bundle, timeout=6.0):
    sh("open", "-b", bundle)
    t = time.time()
    while time.time() - t < timeout:
        if frontmost() == bundle:
            time.sleep(0.4)
            return True
        time.sleep(0.2)
    return False


def screenshot(path):
    sh("screencapture", "-x", path)


# ---------------------------------------------------------------- TextEdit via Accessibility
def _attr(el, name):
    err, val = AS.AXUIElementCopyAttributeValue(el, name, None)
    return val if err == 0 else None


def _find_text_area(el, depth=0):
    if el is None or depth > 8:
        return None
    if _attr(el, "AXRole") == "AXTextArea":
        return el
    for ch in _attr(el, "AXChildren") or []:
        found = _find_text_area(ch, depth + 1)
        if found is not None:
            return found
    return None


def te_area():
    pid = pid_of(TEXTEDIT)
    if not pid:
        return None
    app = AS.AXUIElementCreateApplication(pid)
    for w in [_attr(app, "AXFocusedWindow")] + list(_attr(app, "AXWindows") or []):
        a = _find_text_area(w)
        if a is not None:
            return a
    return None


def te_text():
    a = te_area()
    if a is not None:
        v = _attr(a, "AXValue")
        if v is not None:
            return str(v)
    try:
        out = subprocess.run(["osascript", "-e", 'tell application "TextEdit" to get text of document 1'],
                             capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return None
    return out.stdout.rstrip("\n") if out.returncode == 0 else None


def te_clear():
    a = te_area()
    if a is not None and AS.AXUIElementSetAttributeValue(a, "AXValue", "") == 0 and te_text() == "":
        return True
    activate(TEXTEDIT)
    post_combo(0, Q.kCGEventFlagMaskCommand)    # Cmd+A
    post_combo(51, 0)                           # Delete
    time.sleep(0.3)
    return te_text() == ""


# ---------------------------------------------------------------- the app under test
class App:
    def __init__(self, app_path, d, launch):
        self.app_path, self.dir, self.launch = app_path, d, launch
        self.log_path = os.path.join(d, "e2e.log")
        self.cmd_path = os.path.join(d, "cmd.txt")
        self.proc = None
        self.pid = None

    def start(self):
        for p in (self.log_path, self.cmd_path):
            if os.path.exists(p):
                os.remove(p)
        args = ["--e2e", self.dir, "--diag", os.path.join(self.dir, "diag.json")]
        out = os.path.join(self.dir, "app-output.log")
        env_fault = os.path.join(self.dir, "fault.log")
        if self.launch == "open":
            envs = ["--env", f"HT_FAULT_LOG={env_fault}"]
            subprocess.run(["open", "-n", "--stdout", out, "--stderr", out] + envs +
                           [self.app_path, "--args"] + args, check=False)
        else:
            exe = os.path.join(self.app_path, "Contents", "MacOS", "HumanTyper")
            env = dict(os.environ, HT_FAULT_LOG=env_fault)
            self.proc = subprocess.Popen([exe] + args, stdout=open(out, "w"), stderr=subprocess.STDOUT, env=env)
        ev = self.wait(lambda e: e["ev"] == "start", 40, since=0)
        if not ev:
            raise SystemExit("FAIL: the app did not start (no 'start' event in e2e.log)")
        self.pid = ev["pid"]
        time.sleep(3)

    def events(self):
        try:
            with open(self.log_path, encoding="utf-8") as f:
                return [json.loads(x) for x in f if x.strip()]
        except FileNotFoundError:
            return []

    def mark(self):
        return len(self.events())

    def wait(self, pred, timeout, since=None):
        since = self.mark() if since is None else since
        t = time.time()
        while time.time() - t < timeout:
            for e in self.events()[since:]:
                if pred(e):
                    return e
            time.sleep(0.1)
        return None

    def send(self, **cmd):
        with open(self.cmd_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(cmd) + "\n")

    def info(self):
        m = self.mark()
        self.send(cmd="info")
        return self.wait(lambda e: e["ev"] == "info", 10, since=m) or {}

    def configure(self, text, wpm, typo=0, revise="end", locate="auto"):
        m = self.mark()
        self.send(cmd="config", text=text, wpm=wpm, typo=typo, revise=revise, locate=locate, overlay=True)
        return self.wait(lambda e: e["ev"] == "configured", 10, since=m)

    def state(self):
        st = [e for e in self.events() if e["ev"] == "state"]
        return st[-1] if st else {}

    def wait_idle(self, timeout=10):
        t = time.time()
        while time.time() - t < timeout:
            if self.state().get("st") == "idle":
                return True
            time.sleep(0.2)
        return False

    def quit(self):
        self.send(cmd="quit")
        time.sleep(2)
        if self.pid:
            sh("kill", str(self.pid))


# ---------------------------------------------------------------- cases
class Runner:
    def __init__(self, app, d):
        self.app, self.dir = app, d
        self.results = []

    def _prepare(self, name, text, wpm, locate="auto"):
        self.app.wait_idle(15)
        self.app.configure(text, wpm, locate=locate)
        ok_clear = te_clear()
        front = activate(TEXTEDIT)
        screenshot(os.path.join(self.dir, f"{name}-before.png"))
        return ok_clear, front

    def _finish(self, name, mark, timeout):
        fin = self.app.wait(lambda e: e["ev"] == "event" and e.get("kind") == "finished", timeout, since=mark)
        time.sleep(1.0)
        txt = te_text()
        screenshot(os.path.join(self.dir, f"{name}-after.png"))
        with open(os.path.join(self.dir, f"textedit-{name}.txt"), "w", encoding="utf-8") as f:
            f.write("" if txt is None else txt)
        return fin, txt

    def record(self, name, ok, **kw):
        kw.update(case=name, ok=bool(ok))
        self.results.append(kw)
        print(("PASS " if ok else "FAIL ") + name + "  " + json.dumps(kw, default=str, ensure_ascii=False),
              flush=True)

    def case_key(self, name, fn, locate="auto"):
        ok_clear, front = self._prepare(name, SOURCE, 200, locate)
        m = self.app.mark()
        post_key(KC_F8, fn=fn)
        got = self.app.wait(lambda e: e["ev"] == "event" and e.get("kind") == "hotkey", 5, since=m)
        fin, txt = self._finish(name, m, 60)
        if name == "f8":                     # what does the AX locator see in TextEdit?
            mm = self.app.mark()
            self.app.send(cmd="axprobe")
            print("axprobe: " + json.dumps(self.app.wait(lambda e: e["ev"] == "axprobe", 15, since=mm),
                                           default=str, ensure_ascii=False), flush=True)
        logs = [e.get("val") for e in self.app.events()[m:] if e.get("kind") == "log" and "REV" in str(e.get("val"))]
        self.record(name, txt == EXPECTED, textedit=txt, hotkey_seen=bool(got), locate_log=logs,
                    finished=(fin or {}).get("val"), cleared=ok_clear, textedit_front=front,
                    front_after=frontmost())

    def _ov_button(self, info, name):
        b = (info.get("overlay") or {}).get("buttons", {}).get(name) or {}
        return b.get("cx"), b.get("cy")

    def case_overlay_start(self):
        name = "overlay_start"
        ok_clear, front = self._prepare(name, SOURCE, 200)
        info = self.app.info()
        x, y = self._ov_button(info, "start")
        m = self.app.mark()
        if x is None:
            return self.record(name, False, error="no overlay START coordinates", info=info)
        click(x, y)
        clicked = self.app.wait(lambda e: e["ev"] == "overlay_click", 3, since=m)
        fin, txt = self._finish(name, m, 60)
        self.record(name, txt == EXPECTED, textedit=txt, click_seen=bool(clicked), at=(x, y),
                    finished=(fin or {}).get("val"), cleared=ok_clear, textedit_front=front,
                    front_after=frontmost())

    def _wait_state(self, pred, timeout, since):
        return self.app.wait(lambda e: e["ev"] == "state" and pred(e), timeout, since=since)

    def case_pause_stop(self, name, use_overlay):
        """Start a long text, pause, check nothing is typed while paused, resume, stop."""
        ok_clear, front = self._prepare(name, LONG, 90)
        info = self.app.info()
        steps = {}
        m0 = self.app.mark()

        def press(which):
            if use_overlay:
                x, y = self._ov_button(self.app.info(), which)
                click(x, y)
            else:
                post_key(KC_F9 if which == "pause" else KC_F8, fn=True)

        press("start")
        steps["typing"] = bool(self._wait_state(lambda e: e["st"] == "typing", 8, m0))
        time.sleep(2.5)
        m = self.app.mark()
        press("pause")
        steps["paused"] = bool(self._wait_state(lambda e: e.get("paused"), 5, m))
        time.sleep(0.8)
        n1 = len(te_text() or "")
        time.sleep(2.0)
        n2 = len(te_text() or "")
        steps["halted_while_paused"] = n1 == n2 and n1 > 0
        m = self.app.mark()
        press("pause")                      # RESUME (same button / F9)
        steps["resumed"] = bool(self._wait_state(lambda e: e["st"] == "typing" and not e.get("paused"), 5, m))
        if use_overlay:
            activate(TEXTEDIT) if frontmost() != TEXTEDIT else None
        time.sleep(2.0)
        n3 = len(te_text() or "")
        steps["grew_after_resume"] = n3 > n2
        m = self.app.mark()
        press("stop")
        fin = self.app.wait(lambda e: e["ev"] == "event" and e.get("kind") == "finished", 10, since=m)
        steps["stopped"] = (fin or {}).get("val") == "stopped"
        time.sleep(1.0)
        txt = te_text() or ""
        steps["prefix_ok"] = len(txt) > 10 and LONG.startswith(txt)
        screenshot(os.path.join(self.dir, f"{name}-after.png"))
        with open(os.path.join(self.dir, f"textedit-{name}.txt"), "w", encoding="utf-8") as f:
            f.write(txt)
        self.record(name, all(steps.values()), steps=steps, chars=(n1, n2, n3, len(txt)),
                    textedit=txt[:80], cleared=ok_clear, textedit_front=front, front_after=frontmost())

    def case_setup_window(self, start_info):
        """Permissions are pre-granted: the SETUP window must not open by itself; opened by hand it
        shows every permission granted, says all set, and closes on its own."""
        name = "setup_window"
        self.app.wait_idle(15)
        auto_opened = (start_info or {}).get("setup")
        m = self.app.mark()
        self.app.send(cmd="setup")
        self.app.wait(lambda e: e["ev"] == "setup_opened", 5, since=m)
        time.sleep(1.8)
        rep = self.app.info().get("setup") or {}
        screenshot(os.path.join(self.dir, f"{name}.png"))
        time.sleep(4.5)
        after = self.app.info().get("setup")
        steps = {"not_auto_opened": auto_opened is None,
                 "all_granted": bool(rep.get("status")) and all(v is True for v in rep["status"].values()),
                 "state_ok": rep.get("state") == "ok",
                 "no_restart_button": rep.get("restart_button") is False,
                 "closed_by_itself": after is None}
        self.record(name, all(steps.values()), steps=steps, report=rep)

    def case_main_start(self):
        """Main window [ START ] button: 3 s countdown, the user clicks back into TextEdit meanwhile."""
        name = "main_start"
        self.app.wait_idle(15)
        self.app.configure(SOURCE, 200)
        ok_clear = te_clear()
        activate(APP_ID)
        info = self.app.info()
        b = (info.get("main_buttons") or {}).get("start") or {}
        if b.get("cx") is None:
            return self.record(name, False, error="no main START coordinates", info=info)
        m = self.app.mark()
        click(b["cx"], b["cy"])
        armed = self._wait_state(lambda e: e["st"] == "countdown", 3, m)
        front = activate(TEXTEDIT)
        fin, txt = self._finish(name, m, 60)
        self.record(name, txt == EXPECTED, textedit=txt, countdown=bool(armed), at=(b["cx"], b["cy"]),
                    finished=(fin or {}).get("val"), cleared=ok_clear, textedit_front=front)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--launch", choices=("open", "direct"), default="open")
    ap.add_argument("--cases", default="setup_window,f8,fn_f8,overlay_start,overlay_pause_stop,"
                                       "hotkey_pause_stop,main_start,locate_clipboard,locate_count")
    a = ap.parse_args()
    d = os.path.abspath(a.dir)
    os.makedirs(d, exist_ok=True)
    print(f"helper: AXIsProcessTrusted={AS.AXIsProcessTrusted()} "
          f"post={Q.CGPreflightPostEventAccess()} listen={Q.CGPreflightListenEventAccess()}", flush=True)
    app = App(os.path.abspath(a.app), d, a.launch)
    app.start()
    info = app.info()
    print("app info: " + json.dumps(info, indent=1, default=str), flush=True)
    r = Runner(app, d)
    cases = {
        "f8": lambda: r.case_key("f8", fn=False),
        "fn_f8": lambda: r.case_key("fn_f8", fn=True),
        "locate_clipboard": lambda: r.case_key("locate_clipboard", fn=True, locate="clipboard"),
        "locate_count": lambda: r.case_key("locate_count", fn=True, locate="count"),
        "overlay_start": r.case_overlay_start,
        "overlay_pause_stop": lambda: r.case_pause_stop("overlay_pause_stop", True),
        "hotkey_pause_stop": lambda: r.case_pause_stop("hotkey_pause_stop", False),
        "main_start": r.case_main_start,
        "setup_window": lambda: r.case_setup_window(info),
        # permission probe only: does macOS treat this build as trusted?
        "probe": lambda: r.record("probe", bool(app.info().get("ax_trusted")),
                                  ax_trusted=app.info().get("ax_trusted"),
                                  hotkeys=app.info().get("hotkeys")),
    }
    for c in [c.strip() for c in a.cases.split(",") if c.strip()]:
        try:
            cases[c]()
        except Exception as e:
            import traceback
            r.record(c, False, error=traceback.format_exc())
    final_info = app.info()
    app.quit()
    summary = {"launch": a.launch, "app_info": info, "final_info": final_info, "results": r.results,
               "passed": all(x["ok"] for x in r.results) and bool(r.results)}
    with open(os.path.join(d, "results.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, default=str, ensure_ascii=False)
    print("=" * 60)
    for x in r.results:
        print(f"{'PASS' if x['ok'] else 'FAIL'}  {x['case']:<22} textedit={str(x.get('textedit'))[:70]!r}")
    print("E2E " + ("PASSED" if summary["passed"] else "FAILED"), flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
