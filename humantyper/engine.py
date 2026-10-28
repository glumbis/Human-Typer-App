"""Typing engine: human-like typing, typos, and the [first][revised] revisions."""
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .parser import parse, Plan
from .timing import HumanTiming

BOUNDARY = " \n\t.!?,;:"
SLICE = 0.05

_ROWS = ["qwertyuiopå", "asdfghjkløæ", "zxcvbnm"]
_DIGITS = "1234567890"


def _build_neighbors() -> Dict[str, List[str]]:
    nb: Dict[str, List[str]] = {}
    for r, row in enumerate(_ROWS):
        for c, ch in enumerate(row):
            cand = []
            for dr, dc in ((0, -1), (0, 1), (-1, 0), (-1, 1), (1, -1), (1, 0)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < len(_ROWS) and 0 <= cc < len(_ROWS[rr]):
                    cand.append(_ROWS[rr][cc])
            nb[ch] = cand
    for c, ch in enumerate(_DIGITS):
        nb[ch] = [_DIGITS[x] for x in (c - 1, c + 1) if 0 <= x < len(_DIGITS)]
    return nb


NEIGHBORS = _build_neighbors()


class Stopped(Exception):
    pass


@dataclass
class Settings:
    wpm: float = 60.0
    typo_rate: float = 0.02          # fraction 0..0.10
    revise_mode: str = "random"      # "random" | "end"
    seed: Optional[int] = None
    trigger_min: int = 60
    trigger_max: int = 400


@dataclass
class _Rev:
    index: int
    start: int          # position in the live buffer
    first: str
    revised: str
    end_plan: int       # end of `first` in first-pass coordinates
    offset: int = 0

    @property
    def end(self) -> int:
        return self.start + len(self.first)


def _word_at(s: str, j: int) -> str:
    if j >= len(s) or not s[j].isalpha():
        return ""
    a = j
    while a > 0 and s[a - 1].isalpha():
        a -= 1
    b = j
    while b < len(s) and s[b].isalpha():
        b += 1
    return s[a:b]


class TypingEngine:
    def __init__(self, backend, settings: Optional[Settings] = None,
                 on_progress: Optional[Callable[[float], None]] = None,
                 on_status: Optional[Callable[[str], None]] = None,
                 on_finish: Optional[Callable[[str], None]] = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.backend = backend
        self.settings = settings or Settings()
        self.on_progress = on_progress
        self.on_status = on_status
        self.on_finish = on_finish
        self._sleep = sleep
        self._stop = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._thread: Optional[threading.Thread] = None
        self._kind = "idle"
        self._buf: List[str] = []
        self._cur = 0

    # ---- control --------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def start(self, text: str) -> None:
        if self.running:
            return
        self._stop.clear()
        self._resume.set()
        self._thread = threading.Thread(target=self._thread_main, args=(text,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._resume.set()

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def toggle_pause(self) -> None:
        if self.paused:
            self.resume()
        else:
            self.pause()

    def _thread_main(self, text: str) -> None:
        result = "error"
        try:
            result = "done" if self.run(text, _reset_flags=False) else "stopped"
        except Exception:
            self._emit_status("error")
        finally:
            if self.on_finish:
                try:
                    self.on_finish(result)
                except Exception:
                    pass

    # ---- callbacks ------------------------------------------------------
    def _emit_status(self, kind: str) -> None:
        if kind != "paused":
            self._kind = kind
        if self.on_status:
            self.on_status(kind)

    def _progress(self) -> None:
        if self.on_progress and self._total:
            self.on_progress(min(1.0, self._done / self._total))

    # ---- low level ------------------------------------------------------
    def _check(self) -> None:
        if self._stop.is_set():
            raise Stopped()
        if not self._resume.is_set():
            prev = self._kind
            self._emit_status("paused")
            while not self._resume.is_set():
                if self._stop.is_set():
                    raise Stopped()
                self._resume.wait(SLICE)
            if self._stop.is_set():
                raise Stopped()
            self._emit_status(prev)

    def _wait(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            self._check()
            s = min(SLICE, remaining)
            self._sleep(s)
            remaining -= s
        self._check()

    def _key_char(self, ch: str) -> None:
        self._check()
        b = self.backend
        if ch == "\n":
            b.press_enter()
        elif ch == "\t":
            b.press_tab()
        else:
            b.type_char(ch)
        self._buf.insert(self._cur, ch)
        self._cur += 1

    def _key_backspace(self) -> None:
        self._check()
        self.backend.backspace()
        if self._cur > 0:
            del self._buf[self._cur - 1]
            self._cur -= 1

    def _key_left(self) -> None:
        self._check()
        self.backend.left()
        if self._cur > 0:
            self._cur -= 1

    def _key_right(self) -> None:
        self._check()
        self.backend.right()
        if self._cur < len(self._buf):
            self._cur += 1

    # ---- typing with typos ---------------------------------------------
    def _type_one(self, ch: str, word: str, count: bool) -> None:
        prev = self._buf[self._cur - 1] if self._cur > 0 else ""
        self._wait(self.timing.char_delay(ch, prev, word))
        self._key_char(ch)
        if count:
            self._done += 1
            self._progress()

    def _plan_typo(self, s: str, j: int):
        """Return (wrong_chars, consumed) or None if no typo is possible here."""
        c = s[j]
        rng = self.rng
        if c.isspace():
            return None
        n = len(s)
        if (j + 1 < n and s[j].isalpha() and s[j + 1].isalpha() and s[j] != s[j + 1]
                and rng.random() < 0.3):
            return [s[j + 1], s[j]], 2
        cand = NEIGHBORS.get(c.lower())
        if not cand:
            return None
        w = rng.choice(cand)
        if c.isupper():
            w = w.upper()
        return [w], 1

    def _type_run(self, s: str, typos: bool = True,
                  after: Optional[Callable[[int], None]] = None) -> None:
        rate = max(0.0, self.settings.typo_rate) if typos else 0.0
        j, n = 0, len(s)
        while j < n:
            if rate > 0 and self.rng.random() < rate:
                plan = self._plan_typo(s, j)
                if plan is not None:
                    j = self._do_typo(s, j, *plan)
                    if after:
                        after(j)
                    continue
            self._type_one(s[j], _word_at(s, j), True)
            j += 1
            if after:
                after(j)

    def _do_typo(self, s: str, j: int, wrong: List[str], consumed: int) -> int:
        k = self.rng.choice([0, 0, 1, 1, 2, 3])
        extra = []
        for ch in s[j + consumed: j + consumed + k]:
            if ch in "\n\t":
                break
            extra.append(ch)
        for ch in wrong:
            self._type_one(ch, "", False)
        for idx, ch in enumerate(extra):
            self._type_one(ch, _word_at(s, j + consumed + idx), False)
        self._wait(self.timing.typo_reaction())
        total = consumed + len(extra)
        for _ in range(total):
            self._wait(self.timing.backspace_delay())
            self._key_backspace()
        for idx in range(total):
            self._type_one(s[j + idx], _word_at(s, j + idx), True)
        return j + total

    # ---- revisions ------------------------------------------------------
    def _apply_revision(self, r: _Rev) -> None:
        self._emit_status("revising")
        n = len(self._buf) - r.end            # chars between end of `first` and cursor
        # cursor is always at the end of the buffer between operations
        for _ in range(n):
            self._wait(self.timing.arrow_delay())
            self._key_left()
        for _ in range(len(r.first)):
            self._wait(self.timing.backspace_delay())
            self._key_backspace()
        self._wait(self.timing.revision_think())
        self._type_run(r.revised, typos=True)
        for _ in range(n):
            self._wait(self.timing.arrow_delay())
            self._key_right()
        delta = len(r.revised) - len(r.first)
        for o in self._pending:
            if o is not r and o.index > r.index:
                o.start += delta
        self._pending.remove(r)
        self._emit_status("typing")

    # ---- main -----------------------------------------------------------
    def run(self, text: str, _reset_flags: bool = True) -> bool:
        """Type `text`. Blocking. Returns True if completed, False if stopped."""
        if _reset_flags:
            self._stop.clear()
            self._resume.set()
        plan = parse(text)
        self.rng = random.Random(self.settings.seed)
        self.timing = HumanTiming(self.settings.wpm, self.rng)
        self._buf, self._cur = [], 0
        self._total = len(plan.text) + sum(len(r.revised) for r in plan.revisions)
        self._done = 0
        mode = self.settings.revise_mode
        self._pending = [
            _Rev(r.index, r.start, r.first, r.revised, r.end,
                 self.rng.randint(self.settings.trigger_min, self.settings.trigger_max))
            for r in plan.revisions
        ]
        s = plan.text

        def after(j: int) -> None:
            if mode != "random" or j >= len(s):
                return
            while True:
                ready = [r for r in self._pending
                         if j >= r.end_plan + r.offset
                         and (s[j - 1] in BOUNDARY or j >= r.end_plan + r.offset + 40)]
                if not ready:
                    return
                ready.sort(key=lambda r: (r.end_plan + r.offset, r.index))
                self._apply_revision(ready[0])

        try:
            self._emit_status("typing")
            self._progress()
            self._type_run(s, typos=True, after=after)
            order = list(self._pending)
            if mode == "random":
                self.rng.shuffle(order)
            for r in order:
                self._apply_revision(r)
            self._progress()
            self._emit_status("done")
            return True
        except Stopped:
            self._emit_status("stopped")
            return False
