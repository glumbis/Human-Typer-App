"""macOS-specific logic, exercised on any OS through monkeypatched plat flags and fakes."""
import os
import signal
from types import SimpleNamespace

import pytest

from humantyper import plat
from humantyper.ai import engine as E
from humantyper.ai import models as M
from humantyper.ui import macutil, theme as T, winutil


def set_os(monkeypatch, mac):
    monkeypatch.setattr(plat, "IS_MAC", mac)
    monkeypatch.setattr(plat, "IS_WIN", not mac)


# ---- fonts -----------------------------------------------------------------------
def test_font_candidates_and_factor(monkeypatch):
    assert T.FONT_CANDIDATES[0] == "Consolas"
    assert {"SF Mono", "Menlo", "Monaco"} <= set(T.FONT_CANDIDATES)
    set_os(monkeypatch, False)
    assert T.scale_size(10) == 10
    set_os(monkeypatch, True)
    assert T.scale_size(10) == 12 and T.scale_size(6) == 8 and T.scale_size(1) >= 1


def test_init_fonts_picks_mac_font(monkeypatch):
    monkeypatch.setattr(T.tkfont, "families", lambda root: ["Helvetica", "Menlo", "Monaco"])
    assert T.init_fonts(None) == "Menlo"
    monkeypatch.setattr(T.tkfont, "families", lambda root: ["Consolas", "Menlo"])
    assert T.init_fonts(None) == "Consolas"


# ---- engine ----------------------------------------------------------------------
class Proc:
    pid = 4242
    returncode = None

    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_exe_name_per_os(monkeypatch):
    set_os(monkeypatch, True)
    assert E.exe_name() == "llama-server"
    set_os(monkeypatch, False)
    assert E.exe_name() == "llama-server.exe"


def test_mac_spawn_kwargs(monkeypatch, tmp_path):
    set_os(monkeypatch, True)
    monkeypatch.setattr(plat, "data_dir", lambda: str(tmp_path / "d"))
    seen = {}
    exe = tmp_path / "eng" / "llama-server"
    exe.parent.mkdir()
    exe.write_bytes(b"x")
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")

    def popen(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return Proc()

    e = E.LocalEngine(exe=str(exe), settings={}, popen=popen, health=lambda p: "ok",
                      port_fn=lambda: 5555, start_timeout=1.0, save_settings=lambda d: None)
    assert e.ensure_started(str(model)) == "GPU"
    assert "creationflags" not in seen
    assert seen["start_new_session"] is True
    assert seen["cwd"] == str(exe.parent)
    assert seen["env"]["DYLD_LIBRARY_PATH"].startswith(str(exe.parent))
    assert open(E.pid_path()).read() == "4242"
    e.stop()
    assert not os.path.exists(E.pid_path())


def test_windows_spawn_has_creationflags(monkeypatch, tmp_path):
    set_os(monkeypatch, False)
    monkeypatch.setattr(plat, "data_dir", lambda: str(tmp_path / "d"))
    seen = {}
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    e = E.LocalEngine(exe=str(tmp_path / "llama-server.exe"), settings={},
                      popen=lambda cmd, **kw: (seen.update(kw), Proc())[1], health=lambda p: "ok",
                      port_fn=lambda: 1, use_job=False, start_timeout=1.0, save_settings=lambda d: None)
    e.available = lambda: True
    e.ensure_started(str(model))
    assert seen["creationflags"] == E.CREATE_NO_WINDOW and "start_new_session" not in seen
    assert not os.path.exists(E.pid_path())


def test_cleanup_orphan(monkeypatch, tmp_path):
    set_os(monkeypatch, True)
    monkeypatch.setattr(plat, "data_dir", lambda: str(tmp_path))
    kills = []
    names = {777: "/x/llama-server"}

    def kill(pid, sig):
        kills.append((pid, sig))
        names.pop(pid, None)

    # no pid file
    assert E.cleanup_orphan(kill, names.get and (lambda p: names.get(p, "")), lambda s: None) is False
    # pid file with live llama-server
    E._write_pid(777)
    assert E.cleanup_orphan(kill, lambda p: names.get(p, ""), lambda s: None) is True
    assert kills == [(777, signal.SIGTERM)] and not os.path.exists(E.pid_path())
    # pid reused by some other process: never killed
    E._write_pid(888)
    kills.clear()
    assert E.cleanup_orphan(kill, lambda p: "/usr/bin/python", lambda s: None) is False
    assert kills == [] and not os.path.exists(E.pid_path())
    # garbage
    open(E.pid_path(), "w").write("abc")
    assert E.cleanup_orphan(kill, lambda p: "", lambda s: None) is False


def test_cleanup_orphan_noop_on_windows(monkeypatch):
    set_os(monkeypatch, False)
    assert E.cleanup_orphan() is False


def test_missing_msg_is_os_neutral():
    assert "build.ps1" not in E.MISSING_MSG and "fetch_engine.py" in E.MISSING_MSG


def test_ensure_executable(monkeypatch, tmp_path):
    set_os(monkeypatch, True)
    calls = []
    f = tmp_path / "llama-server"
    f.write_bytes(b"x")
    monkeypatch.setattr(os, "access", lambda p, m: False)
    monkeypatch.setattr(os, "chmod", lambda p, m: calls.append((p, m & 0o111)))
    E.ensure_executable(str(f))
    assert calls and calls[0][1] == 0o111


def test_models_search_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(plat, "data_dir", lambda: str(tmp_path / "HT"))
    dirs = [d.replace("\\", "/") for d in M.default_search_dirs()]
    assert any(d.endswith(".lmstudio/models") for d in dirs)
    assert any(d.endswith(".cache/lm-studio/models") for d in dirs)
    assert dirs[-1].endswith("HT/models")


# ---- focus return ----------------------------------------------------------------
class FakeApp:
    def __init__(self, pid):
        self.pid, self.activated = pid, []

    def processIdentifier(self):
        return self.pid

    def activateWithOptions_(self, opts):
        self.activated.append(opts)


class FakeWS:
    def __init__(self, front):
        self.front = front

    def frontmostApplication(self):
        return self.front


def test_focus_return_remembers_other_app():
    me, notes = FakeApp(100), FakeApp(200)
    ws = FakeWS(notes)
    fr = macutil.FocusReturn(ws, pid=100)
    assert fr.available and fr.poll() is notes
    ws.front = me                       # user clicked our overlay
    assert fr.poll() is notes           # still remembers notes
    assert fr.restore() is True
    assert notes.activated == [macutil.ACTIVATE_IGNORING_OTHER_APPS] and macutil.ACTIVATE_IGNORING_OTHER_APPS == 2


def test_focus_return_noop_when_already_elsewhere_or_unknown():
    me, notes = FakeApp(100), FakeApp(200)
    ws = FakeWS(notes)
    fr = macutil.FocusReturn(ws, pid=100)
    assert fr.restore() is False        # nothing remembered
    fr.poll()
    assert fr.restore() is False and notes.activated == []   # already frontmost
    assert fr.last is notes


def test_focus_return_unavailable_without_pyobjc(monkeypatch):
    monkeypatch.setattr(macutil, "_default_workspace", lambda: None)
    fr = macutil.FocusReturn()
    assert not fr.available and fr.poll() is None and fr.restore() is False


# ---- screen area -----------------------------------------------------------------
def rect(x, y, w, h):
    return SimpleNamespace(origin=SimpleNamespace(x=x, y=y), size=SimpleNamespace(width=w, height=h))


def test_visible_area_flips_cocoa_y():
    # 1440x900 screen, menu bar 25 on top, Dock 70 at the bottom: visibleFrame y(bottom-left)=70, h=805
    screen = SimpleNamespace(frame=lambda: rect(0, 0, 1440, 900), visibleFrame=lambda: rect(0, 70, 1440, 805))
    assert macutil.visible_area(1440, 900, screen) == (0, 25, 1440, 805)


def test_visible_area_fallback():
    class Bad:
        def frame(self):
            raise RuntimeError

    assert macutil.visible_area(1440, 900, Bad()) == (0, 25, 1440, 900 - 25 - 80)


def test_fit_to_workarea_mac(monkeypatch):
    set_os(monkeypatch, True)
    monkeypatch.setattr(macutil, "visible_area", lambda w, h, *a, **k: (0, 25, 1280, 700))
    win = SimpleNamespace(winfo_screenwidth=lambda: 1280, winfo_screenheight=lambda: 800,
                          minsize=lambda *a: setattr(win, "min", a),
                          geometry=lambda g: setattr(win, "geo", g))
    winutil.fit_to_workarea(win, 800, 700, 760, 560)
    assert win.geo == "800x660+240+25" and win.min == (760, 560)


# ---- permissions / misc ----------------------------------------------------------
def test_ax_trusted_none_off_mac(monkeypatch):
    set_os(monkeypatch, False)
    assert macutil.ax_trusted() is None


def test_open_settings_runs_open():
    calls = []
    assert macutil.open_settings(macutil.PANE_INPUT_MONITORING, lambda cmd, **k: calls.append(cmd))
    assert calls == [["open", macutil.PANE_INPUT_MONITORING]]
    assert "Privacy_ListenEvent" in macutil.PANE_INPUT_MONITORING


def test_hotkey_labels(monkeypatch):
    set_os(monkeypatch, True)
    assert macutil.hotkey_labels() == ("fn+F8", "fn+F9")
    set_os(monkeypatch, False)
    assert macutil.hotkey_labels() == ("F8", "F9")


# ---- selftest branching ----------------------------------------------------------
def test_selftest_checks_branch(monkeypatch):
    from humantyper import selftest
    names = lambda: [n for n, _ in selftest.checks(*[lambda: 0] * 10)]
    set_os(monkeypatch, True)
    monkeypatch.setattr(selftest.plat, "IS_MAC", True)
    n = names()
    assert "pyobjc" in n and "ax locator" in n and "uia locator" not in n
    monkeypatch.setattr(selftest.plat, "IS_MAC", False)
    n = names()
    assert "uia locator" in n and "pyobjc" not in n


def test_focus_return_self_front():
    me, notes = FakeApp(100), FakeApp(200)
    ws = FakeWS(me)
    fr = macutil.FocusReturn(ws, pid=100)
    assert fr.self_front() is True
    ws.front = notes
    assert fr.self_front() is False
    assert macutil.FocusReturn(None, pid=1).self_front() in (None, False)


def test_disable_app_nap(monkeypatch):
    calls = []
    pi = SimpleNamespace(beginActivityWithOptions_reason_=lambda o, r: calls.append((o, r)) or "TOKEN")
    assert macutil.disable_app_nap(pi) == "TOKEN"
    opts = calls[0][0]
    assert opts & 0x00FFFFFF and not opts & (1 << 20)        # user-initiated, idle sleep still allowed
    bad = SimpleNamespace(beginActivityWithOptions_reason_=lambda o, r: 1 / 0)
    assert macutil.disable_app_nap(bad) is None
    set_os(monkeypatch, False)
    assert macutil.disable_app_nap() is None


# ---- permission requests / SETUP window logic ------------------------------------
class FakeQuartzPerms:
    def __init__(self, listen=False, post=False, boom=False):
        self.listen, self.post, self.boom, self.calls = listen, post, boom, []

    def _r(self, name, val):
        self.calls.append(name)
        if self.boom:
            raise RuntimeError("tcc")
        return val

    def CGPreflightListenEventAccess(self):
        return self._r("preflight_listen", self.listen)

    def CGPreflightPostEventAccess(self):
        return self._r("preflight_post", self.post)

    def CGRequestListenEventAccess(self):
        return self._r("request_listen", self.listen)

    def CGRequestPostEventAccess(self):
        return self._r("request_post", self.post)


def test_listen_and_post_access():
    q = FakeQuartzPerms(listen=True, post=False)
    assert macutil.listen_access(q) is True and macutil.post_access(q) is False
    assert macutil.listen_access(FakeQuartzPerms(boom=True)) is None          # errors: unknown
    assert macutil.listen_access(SimpleNamespace()) is None                   # old pyobjc/macOS: no API


def test_request_permissions_use_system_prompts():
    q = FakeQuartzPerms()
    prompts = []
    assert macutil.request_accessibility(q, ax_prompt=lambda: prompts.append(1) or False) is False
    assert q.calls == ["request_post"] and prompts == [1]
    assert macutil.request_input_monitoring(q) is False and q.calls[-1] == "request_listen"
    # missing APIs never raise
    assert macutil.request_input_monitoring(SimpleNamespace()) is None
    assert macutil.request_accessibility(SimpleNamespace(), ax_prompt=lambda: True) is True


def test_permission_calls_are_noops_off_mac(monkeypatch):
    set_os(monkeypatch, False)
    assert macutil.listen_access() is None and macutil.post_access() is None
    assert macutil.request_input_monitoring() is None and macutil.request_accessibility() is None


def test_permission_status_and_rows():
    st = macutil.permission_status(ax=lambda: True, listen=lambda: False)
    assert st == {"accessibility": True, "input": False}
    keys = [k for k, *_ in macutil.PERMISSIONS]
    assert keys == ["accessibility", "input"]
    panes = dict((k, p) for k, _t, _w, p in macutil.PERMISSIONS)
    assert panes["accessibility"].endswith("Privacy_Accessibility")
    assert panes["input"].endswith("Privacy_ListenEvent")


@pytest.mark.parametrize("status,hk,fails,want", [
    ({"accessibility": False, "input": True}, False, 0, "missing"),
    ({"accessibility": True, "input": False}, True, 0, "missing"),   # hotkeys on but typing would fail
    ({"accessibility": True, "input": True}, True, 0, "ok"),
    ({"accessibility": True, "input": None}, True, 0, "ok"),         # unknown never nags
    ({"accessibility": True, "input": True}, False, 1, "waiting"),
    ({"accessibility": True, "input": True}, False, 3, "restart"),   # granted but tap still refused
])
def test_setup_state(status, hk, fails, want):
    assert macutil.setup_state(status, hk, fails) == want


def test_bundle_path_and_relaunch_command():
    exe = "/Applications/HumanTyper.app/Contents/MacOS/HumanTyper"
    assert macutil.bundle_path(exe) == "/Applications/HumanTyper.app"
    assert macutil.bundle_path("/usr/bin/python3") is None
    assert macutil.relaunch_command(exe, [exe], True) == ["open", "-n", "/Applications/HumanTyper.app"]
    assert macutil.relaunch_command("/usr/bin/python3", ["main.py"], False) == ["/usr/bin/python3", "main.py"]


def _tk_root():
    import tkinter as tk
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tk")
    r.withdraw()
    T.init_fonts(r)
    return r


def test_setup_window_flow():
    """Missing -> granted: hotkeys retried at once (no restart), all set, closes by itself."""
    from humantyper.ui.setup_window import SetupWindow, OK_MARK, BAD_MARK
    root = _tk_root()
    try:
        perms = {"accessibility": False, "input": False}
        hk = {"ok": False, "tries": 0}
        asked, panes, closed = [], [], []

        def retry():
            hk["tries"] += 1
            hk["ok"] = True
        w = SetupWindow(root, lambda: hk["ok"], retry, lambda: None, status_fn=lambda: perms,
                        request_fns={"accessibility": lambda: asked.append("ax"),
                                     "input": lambda: asked.append("im")},
                        open_pane=panes.append, auto_close_ms=50, on_closed=lambda: closed.append(1))
        assert w.state == "missing" and w.rows["input"][0].cget("text") == BAD_MARK
        assert hk["tries"] == 0                                  # never retried while missing
        w.grant("input", macutil.PANE_INPUT_MONITORING)
        assert asked == ["im"] and panes == [macutil.PANE_INPUT_MONITORING]
        perms.update(accessibility=True, input=True)             # user flips both switches
        w.poll()
        assert w.state == "ok" and hk["tries"] == 1 and w.rows["accessibility"][0].cget("text") == OK_MARK
        assert w.report()["closing"]
        for _ in range(20):
            root.update()
            if closed:
                break
            root.after(20)
        assert closed == [1]
    finally:
        root.destroy()


def test_setup_window_offers_restart_when_tap_keeps_failing():
    from humantyper.ui.setup_window import SetupWindow
    root = _tk_root()
    try:
        w = SetupWindow(root, lambda: False, lambda: None, lambda: None,
                        status_fn=lambda: {"accessibility": True, "input": True},
                        request_fns={}, open_pane=lambda p: None, auto_close_ms=None)
        for _ in range(3):
            w.poll()
        assert w.state == "restart" and w.report()["restart_button"]
        w.close()
    finally:
        root.destroy()
