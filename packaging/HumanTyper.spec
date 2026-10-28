# PyInstaller spec.
#   Windows: BOTH dist/HumanTyper.exe (onefile, portable) and dist/HumanTyper/ (onedir, for installer).
#   macOS:   dist/HumanTyper.app (onedir BUNDLE only; onefile .app is slow and unsupported).
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

IS_MAC = sys.platform == "darwin"
if not IS_MAC:
    from PyInstaller.utils.win32.versioninfo import (VSVersionInfo, FixedFileInfo, StringFileInfo,
                                                     StringTable, StringStruct, VarFileInfo, VarStruct)

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ns = {}
exec(open(os.path.join(ROOT, "humantyper", "__init__.py"), encoding="utf-8").read(), ns)
VERSION = ns["__version__"]
vt = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])

if not IS_MAC:
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=vt, prodvers=vt, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Human Typer"),
            StringStruct("FileDescription", "Human Typer"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "HumanTyper"),
            StringStruct("OriginalFilename", "HumanTyper.exe"),
            StringStruct("ProductName", "Human Typer"),
            StringStruct("ProductVersion", VERSION)])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])])])

# list files explicitly (a directory entry was silently skipped for OneDrive placeholder files)
import glob
datas = [(f, "assets") for f in glob.glob(os.path.join(ROOT, "assets", "*")) if os.path.isfile(f)]
datas += collect_data_files("customtkinter")
# bundled local-AI engine (llama.cpp llama-server + ggml dlls), fetched by tools/fetch_engine.py. Models are NOT bundled.
_llama = os.path.join(ROOT, "packaging", "vendor", "llama")
_server = "llama-server" if IS_MAC else "llama-server.exe"
if not os.path.isfile(os.path.join(_llama, _server)):
    raise SystemExit("llama engine missing: run `python tools/fetch_engine.py` (or build.ps1) first")
_engine = [f for f in glob.glob(os.path.join(_llama, "*")) if os.path.isfile(f)
           and not os.path.basename(f).startswith(".")]
binaries = []
if IS_MAC:
    # as binaries so llama-server keeps its exec bit and the dylibs keep their exact names
    binaries += [(f, "llama") for f in _engine if not f.endswith("LICENSE")]
    datas += [(f, "llama") for f in _engine if f.endswith("LICENSE")]
else:
    datas += [(f, "llama") for f in _engine]
hiddenimports = ["pynput.keyboard._win32", "pynput.mouse._win32", "comtypes", "comtypes.client",
                 "comtypes.automation", "comtypes.typeinfo", "comtypes.stream", "comtypes.persist",
                 "comtypes.server", "comtypes.gen", "PIL", "PIL.Image", "PIL.ImageTk",
                 "humantyper.ai", "humantyper.ai.engine", "humantyper.ai.models", "humantyper.ai.prompts",
                 "humantyper.ai.settings", "humantyper.ui.ai_window", "http.client", "ctypes.wintypes"]
hiddenimports += collect_submodules("comtypes.gen")
icon = os.path.join(ROOT, "assets", "icon.ico")
if IS_MAC:
    hiddenimports = [h for h in hiddenimports if not (h.startswith("comtypes") or h.endswith("_win32")
                                                      or h == "ctypes.wintypes")]
    hiddenimports += ["pynput.keyboard._darwin", "pynput.mouse._darwin", "pynput._util.darwin",
                      "objc", "AppKit", "Foundation", "Quartz", "ApplicationServices", "HIServices",
                      "PyObjCTools"]
    icon = os.path.join(ROOT, "assets", "icon.icns")

a = Analysis([os.path.join(ROOT, "main.py")], pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports,
             excludes=["pytest"], noarchive=False)
pyz = PYZ(a.pure)

if IS_MAC:
    exe_mac = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HumanTyper", console=False,
                  icon=icon, upx=False)
    coll_mac = COLLECT(exe_mac, a.binaries, a.datas, name="HumanTyper", upx=False)
    app = BUNDLE(coll_mac, name="HumanTyper.app", icon=icon,
                 bundle_identifier="io.github.glumbis.humantyper",
                 version=VERSION,
                 info_plist={
                     "CFBundleName": "Human Typer",
                     "CFBundleDisplayName": "Human Typer",
                     "CFBundleShortVersionString": VERSION,
                     "CFBundleVersion": VERSION,
                     "NSHighResolutionCapable": True,
                     "LSMinimumSystemVersion": "11.0",
                     "NSAppleEventsUsageDescription":
                         "Human Typer sends keystrokes to the app you are typing into.",
                     "NSAccessibilityUsageDescription":
                         "Human Typer needs Accessibility access to type into other apps and to read "
                         "the focused text field when it revises text.",
                     "NSInputMonitoringUsageDescription":
                         "Human Typer listens for its F8/F9 hotkeys while another app is in front.",
                 })
else:
    # onefile portable
    exe_one = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="HumanTyper", console=False,
                  icon=icon, version=version_info, upx=False)

    # onedir (installer payload)
    exe_dir = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HumanTyper", console=False,
                  icon=icon, version=version_info, upx=False)
    coll = COLLECT(exe_dir, a.binaries, a.datas, name="HumanTyper", upx=False)
