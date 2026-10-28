"""Model discovery, the installable catalog and a resumable downloader (stdlib only)."""
import hashlib
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional

HF_BASE = "https://huggingface.co"


def app_models_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "HumanTyper", "models")


def default_search_dirs() -> List[str]:
    home = os.path.expanduser("~")
    return [os.path.join(home, ".lmstudio", "models"),
            os.path.join(home, ".cache", "lm-studio", "models"),
            app_models_dir()]


@dataclass(frozen=True)
class ModelInfo:
    path: str
    name: str
    size: int
    source: str          # "app" (downloaded by Human Typer) | "external" (LM Studio etc.)

    @property
    def deletable(self) -> bool:
        return self.source == "app"


def friendly_name(filename: str) -> str:
    stem = filename[:-5] if filename.lower().endswith(".gguf") else filename
    m = re.match(r"^(.*?)[-_.](I?Q\d[\w]*|F16|BF16|F32)$", stem, re.I)
    return f"{m.group(1)} - {m.group(2)}" if m else stem


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def discover(dirs: Optional[List[str]] = None) -> List[ModelInfo]:
    """All usable .gguf files under the search dirs (mmproj / vision projectors skipped)."""
    app_dir = os.path.normcase(os.path.abspath(app_models_dir()))
    out, seen = [], set()
    for d in (dirs if dirs is not None else default_search_dirs()):
        if not os.path.isdir(d):
            continue
        is_app = os.path.normcase(os.path.abspath(d)) == app_dir
        for dp, _dn, files in os.walk(d):
            for fn in files:
                low = fn.lower()
                if not low.endswith(".gguf") or low.startswith("mmproj"):
                    continue
                full = os.path.abspath(os.path.join(dp, fn))
                key = os.path.normcase(full)
                if key in seen:
                    continue
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if size < 1_000_000:      # truncated / placeholder
                    continue
                seen.add(key)
                out.append(ModelInfo(full, friendly_name(fn), size, "app" if is_app else "external"))
    out.sort(key=lambda m: (m.size, m.name.lower()))
    return out


def pick_default(models: List[ModelInfo]) -> Optional[ModelInfo]:
    """Gemma 4 E2B if present, else the smallest installed model."""
    for m in models:
        if "gemma-4-e2b" in m.name.lower():
            return m
    return min(models, key=lambda m: m.size) if models else None


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    name: str
    repo: str
    filename: str
    size: int
    sha256: str
    note: str

    @property
    def url(self) -> str:
        return f"{HF_BASE}/{self.repo}/resolve/main/{self.filename}"


CATALOG = [
    CatalogEntry("gemma4-e2b", "Gemma 4 E2B Instruct", "lmstudio-community/gemma-4-E2B-it-GGUF",
                 "gemma-4-E2B-it-Q4_K_M.gguf", 3427880384,
                 "71e6e8cb64a76da1a734fb8f6ba389d2784950d9902f5e87244fca0a15190c94",
                 "best quality, good Norwegian"),
    CatalogEntry("qwen3-1.7b", "Qwen3 1.7B", "lmstudio-community/Qwen3-1.7B-GGUF",
                 "Qwen3-1.7B-Q4_K_M.gguf", 1282439328,
                 "e0801cbda7e2f3fd00bea4d73b53b422b14b13aa130e778f6414b6b641920b7e",
                 "fast, decent English"),
    CatalogEntry("llama32-1b", "Llama 3.2 1B Instruct", "bartowski/Llama-3.2-1B-Instruct-GGUF",
                 "Llama-3.2-1B-Instruct-Q4_K_M.gguf", 807694464,
                 "6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83",
                 "tiny and very fast, simple text"),
]


def installed_path(entry: CatalogEntry, models: List[ModelInfo]) -> Optional[ModelInfo]:
    """The installed copy of a catalog entry (same file name anywhere in the search dirs)."""
    want = entry.filename.lower()
    for m in models:
        if os.path.basename(m.path).lower() == want:
            return m
    return None


def delete_model(m: ModelInfo) -> bool:
    """Delete a model Human Typer downloaded itself. Never touches LM Studio's files."""
    if not m.deletable:
        return False
    try:
        os.remove(m.path)
        return True
    except OSError:
        return False


# ---- downloader ------------------------------------------------------------
class DownloadCancelled(Exception):
    pass


class DownloadError(Exception):
    pass


def download_file(url: str, dest: str, expected_size: int = 0, sha256: str = "",
                  progress: Optional[Callable[[int, int, float], None]] = None,
                  cancel=None, chunk: int = 1 << 20, timeout: float = 30) -> str:
    """Resumable download to dest (via dest + '.part'). progress(done, total, bytes_per_sec).

    `cancel` is a threading.Event-like object. Cancelling keeps the .part file so a later call resumes.
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if expected_size and have > expected_size:
        os.remove(part)
        have = 0
    req = urllib.request.Request(url, headers={"User-Agent": "HumanTyper/ai"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    resp = None
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:             # asked beyond the end: part is complete (or stale)
            if not (expected_size and have == expected_size):
                os.remove(part)
                return download_file(url, dest, expected_size, sha256, progress, cancel, chunk, timeout)
        else:
            raise DownloadError(f"HTTP {e.code} from server") from e
    except (urllib.error.URLError, OSError) as e:
        raise DownloadError(f"network error: {e}") from e

    if resp is not None:
        with resp:
            status = getattr(resp, "status", 200)
            if have and status != 206:         # server ignored Range: start over
                have = 0
            length = int(resp.headers.get("Content-Length") or 0)
            total = (have + length) if length else expected_size
            if status == 206:
                m = re.match(r"bytes (\d+)-\d+/(\d+)", resp.headers.get("Content-Range") or "")
                if m:
                    total = int(m.group(2))
            done = have
            t0, base, last_report = time.monotonic(), have, 0.0
            try:
                with open(part, "ab" if have else "wb") as f:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled()
                        buf = resp.read(chunk)
                        if not buf:
                            break
                        f.write(buf)
                        done += len(buf)
                        now = time.monotonic()
                        if progress and now - last_report >= 0.2:
                            last_report = now
                            progress(done, total, (done - base) / max(now - t0, 1e-6))
            except DownloadCancelled:
                raise
            except (OSError, urllib.error.URLError) as e:
                raise DownloadError(f"connection lost: {e}") from e
            if progress:
                progress(done, total, (done - base) / max(time.monotonic() - t0, 1e-6))
            if total and done != total:
                raise DownloadError(f"incomplete download ({done} of {total} bytes) - retry to resume")

    size = os.path.getsize(part)
    if expected_size and size != expected_size:
        os.remove(part)
        raise DownloadError(f"size mismatch: got {size}, expected {expected_size}")
    if sha256:
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        if h.hexdigest().lower() != sha256.lower():
            os.remove(part)
            raise DownloadError("checksum mismatch - file removed, try again")
    os.replace(part, dest)
    return dest


def install(entry: CatalogEntry, progress=None, cancel=None, url: str = None, dest_dir: str = None) -> str:
    dest = os.path.join(dest_dir or app_models_dir(), entry.filename)
    return download_file(url or entry.url, dest, entry.size, entry.sha256, progress, cancel)
