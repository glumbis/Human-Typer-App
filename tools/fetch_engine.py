"""Download the pinned llama.cpp (Vulkan x64) Windows release into packaging/vendor/llama.

Usage: python tools/fetch_engine.py [--force]
The zip is verified against a pinned SHA256 before extraction. Bump the three constants together.
"""
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

TAG = "b11910"
ASSET = f"llama-{TAG}-bin-win-vulkan-x64.zip"
SHA256 = "2961f8f525efb118b9e8f83c4ffdfd72237deacee0e226f4a9830caad7962f35"
URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/{ASSET}"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "packaging", "vendor", "llama")
MARKER = os.path.join(DEST, ".engine-version")


def installed() -> bool:
    try:
        with open(MARKER, encoding="utf-8") as f:
            return f.read().strip() == TAG and os.path.isfile(os.path.join(DEST, "llama-server.exe"))
    except OSError:
        return False


def fetch(force=False) -> str:
    if installed() and not force:
        print(f"engine {TAG} already present: {DEST}")
        return DEST
    tmpdir = tempfile.mkdtemp(prefix="ht-engine-")
    zpath = os.path.join(tmpdir, ASSET)
    try:
        print(f"downloading {URL}")
        h = hashlib.sha256()
        with urllib.request.urlopen(URL, timeout=60) as r, open(zpath, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                out.write(b)
                h.update(b)
                done += len(b)
                if total:
                    print(f"\r  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
        print()
        if h.hexdigest().lower() != SHA256:
            raise SystemExit(f"SHA256 mismatch for {ASSET}: got {h.hexdigest()}, expected {SHA256}")
        print("sha256 OK")
        if os.path.isdir(DEST):
            shutil.rmtree(DEST)
        os.makedirs(DEST)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(DEST)
        # some releases nest everything in one folder: flatten
        if not os.path.isfile(os.path.join(DEST, "llama-server.exe")):
            for dp, _dn, fn in os.walk(DEST):
                if "llama-server.exe" in fn:
                    for name in os.listdir(dp):
                        shutil.move(os.path.join(dp, name), os.path.join(DEST, name))
                    break
        if not os.path.isfile(os.path.join(DEST, "llama-server.exe")):
            raise SystemExit("llama-server.exe not found in archive")
        for name in os.listdir(DEST):          # keep only what the server needs (smaller installer)
            if name.lower().endswith(".exe") and name.lower() != "llama-server.exe":
                os.remove(os.path.join(DEST, name))
        with open(MARKER, "w", encoding="utf-8") as f:
            f.write(TAG)
        print(f"engine {TAG} extracted to {DEST}")
        return DEST
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _arg(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


if __name__ == "__main__":
    # build.ps1 passes its own pinned tag/sha so both places must agree
    if _arg("--tag") and _arg("--sha256"):
        if (_arg("--tag"), _arg("--sha256").lower()) != (TAG, SHA256):
            raise SystemExit("build.ps1 and tools/fetch_engine.py pin different llama.cpp releases")
    fetch("--force" in sys.argv)
