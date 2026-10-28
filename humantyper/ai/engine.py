"""LocalEngine: runs the bundled llama.cpp `llama-server.exe` on 127.0.0.1 and streams completions."""
import atexit
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Callable, Iterator, List, Optional

from . import settings as ai_settings
from .. import plat

EXE_NAME = "llama-server.exe"
CREATE_NO_WINDOW = 0x08000000
IDLE_UNLOAD_SECONDS = 600


class EngineError(Exception):
    """kind: missing | start | oom | busy | server"""

    def __init__(self, message, kind="server"):
        super().__init__(message)
        self.kind = kind


# ---- locating the engine -----------------------------------------------------
def engine_dir() -> Optional[str]:
    """Folder containing llama-server.exe: bundled (frozen) or packaging/vendor/llama (source)."""
    root = getattr(sys, "_MEIPASS", None)
    cands = []
    if root:
        cands.append(os.path.join(root, "llama"))
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cands.append(os.path.join(here, "packaging", "vendor", "llama"))
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(os.path.dirname(sys.executable), "llama"))
    for c in cands:
        if os.path.isfile(os.path.join(c, EXE_NAME)):
            return c
    return None


def engine_exe() -> Optional[str]:
    d = engine_dir()
    return os.path.join(d, EXE_NAME) if d else None


MISSING_MSG = ("AI engine not found (llama-server.exe). Run `.\\build.ps1 -FetchEngine` "
               "or `python tools/fetch_engine.py` once, then reopen this window.")


def log_path() -> str:
    return os.path.join(plat.data_dir(), "llama-server.log")


# ---- Windows job object: child dies with the app -----------------------------
def _make_kill_on_close_job():
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000     # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        return job
    except Exception:
        return None


def _assign_to_job(job, proc) -> bool:
    if not job or os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        return bool(k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle))))
    except Exception:
        return False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---- output post-processing --------------------------------------------------
class ThinkStripper:
    """Streaming filter that drops <think>...</think> blocks even when tags are split across chunks."""
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf = ""
        self.inside = False
        self.started = False        # have we emitted any visible text yet (to trim leading blank lines)

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        out = []
        while True:
            if self.inside:
                i = self.buf.find(self.CLOSE)
                if i < 0:
                    keep = _partial_suffix(self.buf, self.CLOSE)
                    self.buf = self.buf[len(self.buf) - keep:] if keep else ""
                    break
                self.buf = self.buf[i + len(self.CLOSE):]
                self.inside = False
            else:
                i = self.buf.find(self.OPEN)
                if i < 0:
                    keep = _partial_suffix(self.buf, self.OPEN)
                    out.append(self.buf[:len(self.buf) - keep])
                    self.buf = self.buf[len(self.buf) - keep:]
                    break
                out.append(self.buf[:i])
                self.buf = self.buf[i + len(self.OPEN):]
                self.inside = True
        return self._trim("".join(out))

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf = ""
        return self._trim(rest)

    def _trim(self, text: str) -> str:
        if not self.started:
            text = text.lstrip()
            if text:
                self.started = True
        return text


def _partial_suffix(s: str, tag: str) -> int:
    for n in range(min(len(tag) - 1, len(s)), 0, -1):
        if s.endswith(tag[:n]):
            return n
    return 0


def parse_sse(lines) -> Iterator[dict]:
    """Yield decoded JSON objects from an iterable of SSE lines (bytes or str); stops at [DONE]."""
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except ValueError:
            continue


def sse_text(lines, stripper: Optional[ThinkStripper] = None, on_timings=None) -> Iterator[str]:
    """Text deltas from SSE lines with <think> stripped. on_timings(dict) gets llama.cpp's timings."""
    st = stripper or ThinkStripper()
    for obj in parse_sse(lines):
        if obj.get("timings") and on_timings:
            on_timings(obj["timings"])
        for ch in obj.get("choices") or []:
            piece = (ch.get("delta") or {}).get("content")
            if piece:
                out = st.feed(piece)
                if out:
                    yield out
    tail = st.flush()
    if tail:
        yield tail


# ---- engine ------------------------------------------------------------------
class LocalEngine:
    def __init__(self, exe: Optional[str] = None, settings: Optional[dict] = None,
                 popen: Callable = subprocess.Popen, health: Optional[Callable[[int], str]] = None,
                 port_fn: Callable[[], int] = free_port, use_job: bool = True,
                 start_timeout: float = 240.0, save_settings: Optional[Callable[[dict], None]] = None):
        self._exe = exe
        self.settings = settings if settings is not None else ai_settings.load()
        self._save = save_settings or ai_settings.save
        self._popen = popen
        self._health = health or self._http_health
        self._port_fn = port_fn
        self._use_job = use_job
        self.start_timeout = start_timeout
        self._job = None
        self._proc = None
        self.port = 0
        self.model_path = ""
        self.mode = ""            # "GPU" | "CPU" once running
        self.last_tps = 0.0
        self.on_state: Optional[Callable[[str], None]] = None
        self._lock = threading.RLock()
        self._gen_lock = threading.Lock()
        self._conn = None
        self._cancel = threading.Event()
        self._last_used = time.monotonic()
        self._idle_timer = None
        self._log_fh = None

    # -- state --
    @property
    def exe(self) -> Optional[str]:
        return self._exe or engine_exe()

    def available(self) -> bool:
        e = self.exe
        return bool(e and os.path.isfile(e))

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def generating(self) -> bool:
        return self._gen_lock.locked()

    def _emit(self, text):
        cb = self.on_state
        if cb:
            try:
                cb(text)
            except Exception:
                pass

    # -- command line --
    def build_cmd(self, model_path: str, port: int, ngl: int) -> List[str]:
        return [self.exe, "--host", "127.0.0.1", "--port", str(port), "-m", model_path,
                "-c", "12288", "--jinja", "-ngl", str(ngl), "--no-webui"]

    # -- start / stop --
    def ensure_started(self, model_path: str) -> str:
        """Start (or reuse) the server for `model_path`. Returns "GPU" or "CPU". Blocking."""
        with self._lock:
            if not self.available():
                raise EngineError(MISSING_MSG, "missing")
            if not os.path.isfile(model_path):
                raise EngineError(f"Model file not found: {model_path}", "start")
            if self.running() and os.path.normcase(self.model_path) == os.path.normcase(model_path):
                self._touch()
                return self.mode
            self.stop()
            attempts = [0] if self.settings.get("force_cpu") else [99, 0]
            last_err = ""
            for ngl in attempts:
                ok, last_err = self._start_once(model_path, ngl)
                if ok:
                    self.mode = "GPU" if ngl else "CPU"
                    self.model_path = model_path
                    if ngl == 0 and len(attempts) == 2:
                        self.settings["force_cpu"] = True
                        self._save(self.settings)
                    self._touch()
                    self._emit(f"ready ({self.mode})")
                    return self.mode
                self.stop()
                if ngl:
                    self._emit("GPU start failed, retrying on CPU...")
            kind = "oom" if _looks_oom(self.log_tail()) else "start"
            raise EngineError(("Not enough memory to load this model. Try a smaller model."
                               if kind == "oom" else f"Engine failed to start: {last_err}"), kind)

    def _start_once(self, model_path: str, ngl: int):
        port = self._port_fn()
        cmd = self.build_cmd(model_path, port, ngl)
        self._emit("loading model..." if ngl else "loading model (CPU)...")
        try:
            os.makedirs(os.path.dirname(log_path()), exist_ok=True)
            self._log_fh = open(log_path(), "wb")
        except OSError:
            self._log_fh = None
        kw = {"stdin": subprocess.DEVNULL, "stdout": self._log_fh or subprocess.DEVNULL,
              "stderr": subprocess.STDOUT, "cwd": os.path.dirname(cmd[0])}
        if os.name == "nt":
            kw["creationflags"] = CREATE_NO_WINDOW
        try:
            self._proc = self._popen(cmd, **kw)
        except OSError as e:
            return False, str(e)
        self.port = port
        if self._use_job:
            if self._job is None:
                self._job = _make_kill_on_close_job()
            _assign_to_job(self._job, self._proc)
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                return False, f"server exited with code {self._proc.returncode}"
            state = self._health(port)
            if state == "ok":
                return True, ""
            time.sleep(0.25)
        return False, "timed out waiting for the model to load"

    def stop(self):
        with self._lock:
            self._cancel_idle()
            p, self._proc = self._proc, None
            self.mode = ""
            self.model_path = ""
            if p is not None and p.poll() is None:
                try:
                    p.terminate()
                    p.wait(timeout=4)
                except Exception:
                    try:
                        p.kill()
                        p.wait(timeout=4)
                    except Exception:
                        pass
            if self._log_fh:
                try:
                    self._log_fh.close()
                except OSError:
                    pass
                self._log_fh = None

    def log_tail(self, n: int = 6000) -> str:
        try:
            with open(log_path(), "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - n))
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""

    # -- idle unload --
    def _touch(self):
        self._last_used = time.monotonic()
        self._cancel_idle()
        t = threading.Timer(IDLE_UNLOAD_SECONDS, self._idle_check)
        t.daemon = True
        t.start()
        self._idle_timer = t

    def _cancel_idle(self):
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _idle_check(self):
        if self.generating():
            self._touch()
        elif time.monotonic() - self._last_used >= IDLE_UNLOAD_SECONDS - 1:
            self.stop()
            self._emit("stopped (idle)")

    # -- health --
    @staticmethod
    def _http_health(port: int) -> str:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                body = json.loads(r.read().decode("utf-8", "replace") or "{}")
                return "ok" if r.status == 200 and body.get("status", "ok") == "ok" else "loading"
        except Exception:
            return "down"

    # -- generation --
    def cancel(self):
        self._cancel.set()
        c = self._conn
        if c is not None:
            try:
                c.close()
            except Exception:
                pass

    def generate(self, messages: list, max_tokens: int = 600, temperature: float = 0.8,
                 extra: Optional[dict] = None) -> Iterator[str]:
        """Stream text chunks for a chat request. Call cancel() from another thread to stop."""
        if not self._gen_lock.acquire(blocking=False):
            raise EngineError("A generation is already running.", "busy")
        try:
            if not self.running():
                raise EngineError("Engine is not running.", "server")
            self._cancel.clear()
            self._touch()
            body = {"messages": messages, "stream": True, "max_tokens": int(max_tokens),
                    "temperature": temperature, "top_p": 0.95,
                    "chat_template_kwargs": {"enable_thinking": False}}
            if extra:
                body.update(extra)
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)
            self._conn = conn
            t0, n_chunks, timings = time.monotonic(), 0, {}
            try:
                conn.request("POST", "/v1/chat/completions", json.dumps(body),
                             {"Content-Type": "application/json"})
                resp = conn.getresponse()
                if resp.status != 200:
                    detail = resp.read(2000).decode("utf-8", "replace")
                    kind = "oom" if _looks_oom(detail + self.log_tail(2000)) else "server"
                    raise EngineError(f"Engine error {resp.status}: {detail[:300]}", kind)
                lines = iter(resp.readline, b"")
                for piece in sse_text(lines, on_timings=timings.update):
                    if self._cancel.is_set():
                        break
                    n_chunks += 1
                    yield piece
            except (OSError, http.client.HTTPException, ValueError):
                if not self._cancel.is_set():
                    if not self.running():
                        raise EngineError("The engine stopped unexpectedly"
                                          + (" (out of memory?)" if _looks_oom(self.log_tail()) else "") + ".",
                                          "oom" if _looks_oom(self.log_tail()) else "server")
                    raise
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
                self._conn = None
                tps = timings.get("predicted_per_second")
                self.last_tps = float(tps) if tps else n_chunks / max(time.monotonic() - t0, 1e-6)
        finally:
            self._gen_lock.release()
            self._touch()


def _looks_oom(text: str) -> bool:
    t = (text or "").lower()
    return any(s in t for s in ("out of memory", "failed to allocate", "erroroutofdevicememory",
                                "erroroutofhostmemory", "std::bad_alloc", "cudamalloc failed"))


# ---- module-level singleton --------------------------------------------------
_engine: Optional[LocalEngine] = None


def get_engine() -> LocalEngine:
    global _engine
    if _engine is None:
        _engine = LocalEngine()
        atexit.register(shutdown)
    return _engine


def shutdown():
    global _engine
    if _engine is not None:
        try:
            _engine.cancel()
            _engine.stop()
        except Exception:
            pass
