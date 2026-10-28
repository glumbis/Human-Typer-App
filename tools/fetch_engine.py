"""Download the pinned llama.cpp release for this OS into packaging/vendor/llama.

Windows: Vulkan x64 zip.  macOS: arm64 (Metal) or x64 tar.gz, chosen by platform.machine() or --arch.
Usage: python tools/fetch_engine.py [--force] [--arch arm64|x64]
The archive is verified against a pinned SHA256 before extraction. Bump the pins together (and build.ps1).
"""
import hashlib
import os
import shutil
import platform
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

TAG = "b11910"
ASSET = f"llama-{TAG}-bin-win-vulkan-x64.zip"
SHA256 = "2961f8f525efb118b9e8f83c4ffdfd72237deacee0e226f4a9830caad7962f35"
URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/{ASSET}"

# macOS assets (tar.gz with a top-level llama-<tag>/ folder holding llama-server + dylibs), keyed by (os, arch)
MAC_ASSETS = {
    ("darwin", "arm64"): (f"llama-{TAG}-bin-macos-arm64.tar.gz",
                          "457f53b710cbdd65ce4e662d4e1aa49accee700110e99a5a49a818d47fc9152f"),
    ("darwin", "x64"): (f"llama-{TAG}-bin-macos-x64.tar.gz",
                        "edbeb0a17a04bedd94143c43f94c8c9c2770fe775e06ea780a96b40ae56f8140"),
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "packaging", "vendor", "llama")
MARKER = os.path.join(DEST, ".engine-version")


def mac_arch(arg=None) -> str:
    a = (arg or platform.machine()).lower()
    return "arm64" if a in ("arm64", "aarch64") else "x64"


def _plan(arch=None):
    """-> (asset, sha256, server binary name, marker text) for this OS."""
    if sys.platform == "darwin":
        asset, sha = MAC_ASSETS[("darwin", mac_arch(arch))]
        return asset, sha, "llama-server", f"{TAG}-{mac_arch(arch)}"
    return ASSET, SHA256, "llama-server.exe", TAG


def installed(arch=None) -> bool:
    _a, _s, server, marker = _plan(arch)
    try:
        with open(MARKER, encoding="utf-8") as f:
            return f.read().strip() == marker and os.path.isfile(os.path.join(DEST, server))
    except OSError:
        return False


def _extract_mac(tpath):
    """Flat-extract llama-server + *.dylib (+ metal files, LICENSE). Symlinks become real copies."""
    dev_links = {"libllama.dylib", "libllama-common.dylib", "libmtmd.dylib"}   # link-time names, never loaded
    keep = lambda n: (n == "llama-server" or n.endswith((".dylib", ".metal", ".metallib")) or n == "LICENSE")         and n not in dev_links
    files, links = {}, {}
    with tarfile.open(tpath, "r:gz") as t:
        for m in t.getmembers():
            name = os.path.basename(m.name.rstrip("/"))
            if m.isfile():
                files[name] = (t.extractfile(m).read(), m.mode)
            elif m.issym():
                links[name] = os.path.basename(m.linkname)
    out = {}
    for name in list(files) + list(links):
        if not keep(name):
            continue
        src, hops = name, 0
        while src in links and hops < 8:
            src, hops = links[src], hops + 1
        if src not in files:
            continue
        out[name] = files[src]
    if "llama-server" not in out:
        raise SystemExit("llama-server not found in archive")
    for name, (data, mode) in out.items():
        p = os.path.join(DEST, name)
        with open(p, "wb") as f:
            f.write(data)
        os.chmod(p, 0o755 if (name == "llama-server" or name.endswith(".dylib") or mode & 0o111) else 0o644)


def fetch(force=False, arch=None) -> str:
    ASSET, SHA256, server, marker = _plan(arch)
    URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/{ASSET}"
    if installed(arch) and not force:
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
        if sys.platform == "darwin":
            _extract_mac(zpath)
            with open(MARKER, "w", encoding="utf-8") as f:
                f.write(marker)
            print(f"engine {marker} extracted to {DEST}")
            return DEST
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
    if _arg("--tag") and _arg("--sha256") and sys.platform != "darwin":
        if (_arg("--tag"), _arg("--sha256").lower()) != (TAG, SHA256):
            raise SystemExit("build.ps1 and tools/fetch_engine.py pin different llama.cpp releases")
    fetch("--force" in sys.argv, _arg("--arch"))
