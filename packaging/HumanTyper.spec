# PyInstaller spec. Builds BOTH: dist/HumanTyper.exe (onefile, portable) and dist/HumanTyper/ (onedir, for installer).
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.utils.win32.versioninfo import (VSVersionInfo, FixedFileInfo, StringFileInfo,
                                                 StringTable, StringStruct, VarFileInfo, VarStruct)

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ns = {}
exec(open(os.path.join(ROOT, "humantyper", "__init__.py"), encoding="utf-8").read(), ns)
VERSION = ns["__version__"]
vt = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])

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
hiddenimports = ["pynput.keyboard._win32", "pynput.mouse._win32", "comtypes", "comtypes.client",
                 "comtypes.automation", "comtypes.typeinfo", "comtypes.stream", "comtypes.persist",
                 "comtypes.server", "comtypes.gen", "PIL", "PIL.Image", "PIL.ImageTk"]
hiddenimports += collect_submodules("comtypes.gen")
icon = os.path.join(ROOT, "assets", "icon.ico")

a = Analysis([os.path.join(ROOT, "main.py")], pathex=[ROOT], datas=datas, hiddenimports=hiddenimports,
             excludes=["pytest"], noarchive=False)
pyz = PYZ(a.pure)

# onefile portable
exe_one = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="HumanTyper", console=False,
              icon=icon, version=version_info, upx=False)

# onedir (installer payload)
exe_dir = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HumanTyper", console=False,
              icon=icon, version=version_info, upx=False)
coll = COLLECT(exe_dir, a.binaries, a.datas, name="HumanTyper", upx=False)
