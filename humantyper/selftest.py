"""Hidden `--selftest <report-file>` check used to verify frozen builds. No GUI, no keystrokes."""
import os
import sys
import traceback


def run(report_path=None) -> int:
    lines, ok = [], True

    def step(name, fn):
        nonlocal ok
        try:
            lines.append(f"OK   {name}: {fn()}")
        except BaseException:
            ok = False
            lines.append(f"FAIL {name}:\n{traceback.format_exc()}")

    def core():
        import humantyper
        from humantyper import app, engine, parser, timing, config, clipboard, hotkeys, backends  # noqa
        from humantyper.ui import art, overlay, stats, widgets, winutil  # noqa
        return f"version {humantyper.__version__}, frozen={getattr(sys, 'frozen', False)}"

    def uia():
        from humantyper.locator import UiaLocator
        loc = UiaLocator()
        snap = loc.snapshot()                     # read-only
        if snap is None and loc.last_error and "comtypes" in loc.last_error.lower():
            raise RuntimeError(loc.last_error)
        return f"snapshot={'None' if snap is None else 'len %d' % len(snap[0])}, last_error={loc.last_error!r}"

    def uia_gen():
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA
        return f"IUIAutomation={UIA.IUIAutomation.__name__}"

    def pyn():
        from pynput.keyboard import Controller, Key  # noqa
        Controller()
        return "Controller created"

    def ctk():
        import customtkinter
        from humantyper.ui import theme as T
        p = T.resource_path(os.path.join("assets", "icon.ico"))
        if not os.path.isfile(p):
            raise FileNotFoundError(p)
        return f"customtkinter {customtkinter.__version__}, icon {p}"

    def pil():
        from PIL import Image  # noqa
        return "PIL ok"

    def ai_engine():
        import subprocess
        from humantyper.ai import engine as E
        exe = E.engine_exe()
        if not exe:
            raise FileNotFoundError("llama-server.exe not bundled: " + E.MISSING_MSG)
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60,
                           cwd=os.path.dirname(exe), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        if r.returncode != 0 or not out:
            raise RuntimeError(f"llama-server --version failed rc={r.returncode}: {out[:3]}")
        return out[0]

    def ai_models():
        from humantyper.ai import models as M
        from humantyper.ui import ai_window  # noqa  (import only)
        found = M.discover()
        return f"{len(found)} model(s) found, catalog {len(M.CATALOG)} entries"

    for n, f in [("core imports", core), ("uia locator", uia), ("uia gen module", uia_gen),
                 ("pynput", pyn), ("customtkinter+icon", ctk), ("pillow", pil),
                 ("ai engine", ai_engine), ("ai models", ai_models)]:
        step(n, f)
    lines.append("RESULT " + ("PASS" if ok else "FAIL"))
    text = "\n".join(lines) + "\n"
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 0 if ok else 1
