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

    for n, f in [("core imports", core), ("uia locator", uia), ("uia gen module", uia_gen),
                 ("pynput", pyn), ("customtkinter+icon", ctk), ("pillow", pil)]:
        step(n, f)
    lines.append("RESULT " + ("PASS" if ok else "FAIL"))
    text = "\n".join(lines) + "\n"
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 0 if ok else 1
