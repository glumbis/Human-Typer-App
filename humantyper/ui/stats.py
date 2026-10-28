"""Live typing statistics (pure Python, no Tk): rolling WPM, elapsed/ETA, typos, revisions."""
import re
import time
from collections import deque

_REV_RE = re.compile(r"REV\w*\s*(\d+)\s*/\s*(\d+)", re.I)


def fmt_time(sec) -> str:
    if sec is None:
        return "--:--"
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Stats:
    WINDOW = 6.0            # seconds of keystrokes used for the live WPM

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset(0)

    def reset(self, total=0):
        self.total = int(total)
        self.typed = 0              # non-backspace keystrokes
        self.typos = 0              # backspaces
        self.rev_done = 0
        self.rev_total = None
        self.progress = 0.0
        self._stamps = deque()
        self._t0 = None
        self._paused_at = None
        self._paused_total = 0.0
        self._frozen_wpm = None
        self._kind = "typing"
        self.running = False

    # -- lifecycle
    def begin(self, total=None):
        if total is not None:
            self.total = int(total)
        self.typed = self.typos = self.rev_done = 0
        self.rev_total = None
        self.progress = 0.0
        self._stamps.clear()
        self._t0 = self.clock()
        self._paused_at = None
        self._paused_total = 0.0
        self._frozen_wpm = None
        self._kind = "typing"
        self.running = True

    def end(self):
        if self._paused_at is not None:
            self.resume()
        el = self.elapsed()
        self.running = False
        self._frozen_wpm = self.typed / 5.0 / (el / 60.0) if el > 1.0 else 0.0   # session average

    def pause(self):
        if self._paused_at is None and self._t0 is not None:
            self._frozen_wpm = self.wpm()
            self._paused_at = self.clock()

    def resume(self):
        if self._paused_at is not None:
            self._paused_total += self.clock() - self._paused_at
            self._paused_at = None
            self._frozen_wpm = None
            self._stamps.clear()

    # -- feeds
    def on_char(self, ch):
        if ch == "\b":
            self.typos += 1
        else:
            self.typed += 1
        self._stamps.append(self.clock())
        self._trim()

    def on_log(self, text):
        m = _REV_RE.search(str(text))
        if m:
            i, n = int(m.group(1)), int(m.group(2))
            if n > 0:
                self.rev_total = n
            self.rev_done = max(self.rev_done, i - 1)       # revision i is in progress

    def on_status(self, kind):
        if kind == "revising":
            self._kind = "revising"
        elif kind == "typing":
            if self._kind == "revising":
                self.rev_done += 1                           # fallback when the log format changes
            self._kind = "typing"

    def on_progress(self, frac):
        self.progress = max(0.0, min(1.0, float(frac)))

    # -- readouts
    def _trim(self):
        cut = self.clock() - self.WINDOW
        while self._stamps and self._stamps[0] < cut:
            self._stamps.popleft()

    def elapsed(self):
        if self._t0 is None:
            return 0.0
        now = self._paused_at if self._paused_at is not None else self.clock()
        return max(0.0, now - self._t0 - self._paused_total)

    def wpm(self):
        if self._paused_at is not None or not self.running:
            return self._frozen_wpm or 0.0
        self._trim()
        if len(self._stamps) < 2:
            return 0.0
        span = min(self.WINDOW, max(0.5, self.clock() - self._stamps[0]))
        return len(self._stamps) / 5.0 / (span / 60.0)

    def eta(self):
        p = self.progress
        if p < 0.02 or p >= 1.0:
            return 0.0 if p >= 1.0 else None
        el = self.elapsed()
        return el * (1.0 - p) / p

    def net_chars(self):
        n = max(0, self.typed - self.typos)
        return min(n, self.total) if self.total else n

    def revisions_text(self):
        return f"{self.rev_done}/{self.rev_total if self.rev_total is not None else '?'}"
