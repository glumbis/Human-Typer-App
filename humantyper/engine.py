"""Typing engine: human-like typing, typos, and the [first][revised] revisions."""
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .parser import parse, Plan
from .timing import HumanTiming
from . import plat
from .locator import choose_occurrence, find_all, normalize

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
    locate_mode: str = "auto"        # "auto" (UIA -> clipboard -> count) | "clipboard" | "count"


CTX_LEN = 16            # preceding-context anchor for empty-`first` insertions
COPY_WAIT = 1.0         # max seconds to wait for the clipboard to change after Ctrl+C
CLIP_MARGIN = 10        # extra chars the backward selection should cover beyond the phrase
CLIP_EXTRA_WORDS = 1    # ...plus this many more word jumps
CLIP_TOL = 50           # a partial-window match may sit this far (+n) from where we expect it
OK, BAD = "✓", "✗"


@dataclass
class _Loc:
    kind: str                       # "uia" | "clipboard" | "count"
    d: int = 0                      # real chars between phrase end and the original caret
    text: str = ""                  # uia: document text before the revision
    caret: int = 0                  # uia: original caret offset


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


def _prev_word_start(buf, pos: int) -> int:
    """Start of the previous word (skip non-word chars, then word chars)."""
    isw = lambda c: c.isalnum() or c in "_'"
    while pos > 0 and not isw(buf[pos - 1]):
        pos -= 1
    while pos > 0 and isw(buf[pos - 1]):
        pos -= 1
    return pos


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
                 sleep: Callable[[float], None] = time.sleep,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_char: Optional[Callable[[str], None]] = None):
        self.on_log = on_log
        self.on_char = on_char
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
        self.error: Optional[str] = None    # traceback of the last crash of the typing thread
        self._kind = "idle"
        self._buf: List[str] = []
        self._cur = 0
        self._clip_verified: set = set()    # clipboard methods whose Left-collapse was verified this run

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
            import traceback
            self.error = traceback.format_exc()
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

    def _log(self, msg: str) -> None:
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception:
                pass

    def _char_cb(self, ch: str) -> None:
        if self.on_char:
            try:
                self.on_char(ch)
            except Exception:
                pass

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
        self._char_cb(ch)
        self._buf.insert(self._cur, ch)
        self._cur += 1

    def _key_backspace(self) -> None:
        self._check()
        self.backend.backspace()
        self._char_cb("\b")
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
        self._log("typo '" + "".join(wrong) + "'->fix")
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
    def _arrow(self, fn) -> None:
        self._wait(self.timing.arrow_delay())
        fn()

    def _press_n(self, fn, n: int) -> None:
        for _ in range(n):
            self._arrow(fn)

    def _copy_text(self) -> Optional[str]:
        """Ctrl+C, wait for the clipboard to change; normalized text or None."""
        b = self.backend
        seq0 = b.clipboard_seq()
        b.copy()
        for _ in range(int(COPY_WAIT / 0.05) + 1):
            if b.clipboard_seq() != seq0:
                t = b.clipboard_get_text()
                return normalize(t) if t else None
            self._check()
            self._sleep(0.05)
        return None

    def _locate_uia(self, tag: str, anchor: str, n: int, ctx: str = "") -> Optional[_Loc]:
        loc = getattr(self.backend, "locator", None)
        if loc is None:
            self._log(f"{tag} // locate: {'AX' if plat.IS_MAC else 'UIA'} unavailable")
            return None
        nm = getattr(loc, "name", "UIA")
        snap = loc.snapshot()
        if not snap:
            why = getattr(loc, "last_error", "") if plat.IS_MAC else ""
            self._log(f"{tag} // locate: {nm} {BAD} (no text pattern{': ' + why if why else ''})")
            return None
        text, caret = snap
        if "\r\n" in text:
            self._log(f"{tag} // locate: {nm} {BAD} (CRLF offsets)")
            return None
        tm = normalize(text)
        end = choose_occurrence(tm[:caret], anchor, n, ctx)
        if end is None:
            self._log(f"{tag} // locate: {nm} {BAD} (phrase not found/ambiguous)")
            return None
        if not loc.select(end, text[:end]):
            loc.select(caret, text[:caret])        # put the caret back before failing over
            self._log(f"{tag} // locate: {nm} {BAD} (select not verified)")
            return None
        self._log(f"{tag} // locate: {nm} {OK}")
        return _Loc("uia", caret - end, text, caret)

    def _word_jumps_for(self, chars: int) -> int:
        """Ctrl+Left presses (Windows edit-control semantics, modelled on our own
        buffer) needed to cover `chars` characters back from the caret."""
        buf = self._buf
        target = max(0, len(buf) - chars)
        pos, k = len(buf), 0
        while pos > target and pos > 0:
            pos = _prev_word_start(buf, pos)
            k += 1
        return k

    def _pick(self, txt: str, anchor: str, n: int, ctx: str, bounded: bool) -> Optional[int]:
        """Occurrence end offset inside the copied tail `txt`; when the selection is only
        a partial window (`bounded`) the occurrence must also be plausibly close to where
        the buffer model expects it (a far-away duplicate is not accepted)."""
        end = choose_occurrence(txt, anchor, n, ctx)
        if end is None:
            return None
        if bounded and abs((len(txt) - end) - n) > CLIP_TOL + n:
            return None
        return end

    def _locate_clipboard(self, tag: str, anchor: str, n: int, ctx: str = "") -> Optional[_Loc]:
        """Caret is at the end of the typed text. Select backwards from it just far
        enough (Ctrl+Shift+Left word jumps), copy, find `anchor`; collapse with Left
        (caret -> selection start) and hop Right to the end of `anchor`. No round trip."""
        b = self.backend
        need = ("copy", "shift_left", "select_to_start", "clipboard_seq", "clipboard_get_text",
                "clipboard_snapshot", "clipboard_restore")
        if not all(hasattr(b, a) for a in need):
            self._log(f"{tag} // clipboard unavailable")
            return None
        snap = b.clipboard_snapshot()
        if snap is None:
            self._log(f"{tag} // clipboard {BAD} (cannot save clipboard)")
            return None
        try:
            self._check()
            grow = getattr(b, "ctrl_shift_left", None)
            txt: Optional[str] = None
            end: Optional[int] = None
            method = "home"
            prev_len = 0
            if grow is not None:
                j0 = self._word_jumps_for(n + len(anchor) + CLIP_MARGIN) + CLIP_EXTRA_WORDS
                for step in (j0, 6, 20):
                    for _ in range(step):
                        self._arrow(grow)
                    got = self._copy_text()
                    if not got or len(got) <= prev_len:      # selection did not grow: unsupported / at start
                        break
                    txt, prev_len = got, len(got)
                    end = self._pick(txt, anchor, n, ctx, True)
                    if end is not None:
                        method = "word"
                        break
            if end is None:
                # last resort for the selection: everything to the document start (same anchor)
                b.select_to_start()
                got = self._copy_text()
                if got:
                    txt = got
                    end = choose_occurrence(txt, anchor, n, ctx)
                if end is None:
                    b.right()                       # collapse back to the caret (right edge)
                    self._log(f"{tag} // clipboard {BAD} ("
                              + ("phrase not found/ambiguous)" if got else "nothing copied)"))
                    return None
            d = len(txt) - end                      # real chars between phrase end and the caret
            b.left()                                # collapse to the selection START
            self._press_n(b.right, end)             # short hop to just after the phrase
            duplicate = len(find_all(txt, anchor)) > 1 if anchor else False
            if anchor and (method not in self._clip_verified or duplicate):
                self._press_n(b.shift_left, len(anchor))
                got = self._copy_text()
                b.right()                           # collapse to the right edge = phrase end
                if got != anchor:
                    self._press_n(b.right, d)       # on to the original caret
                    self._log(f"{tag} // clipboard verify {BAD}")
                    return None
                self._clip_verified.add(method)
                self._log(f"{tag} // locate: clipboard {OK} ({method}, verified)")
            else:
                self._log(f"{tag} // locate: clipboard {OK} ({method})")
            return _Loc("clipboard", d)
        finally:
            try:
                b.clipboard_restore(snap)
            except Exception:
                pass

    def _locate(self, tag: str, anchor: str, n: int, ctx: str = "") -> Optional[_Loc]:
        mode = self.settings.locate_mode
        if mode not in ("auto", "clipboard", "count"):
            mode = "auto"
        if mode == "auto":
            loc = self._locate_uia(tag, anchor, n, ctx)
            if loc:
                return loc
        if mode in ("auto", "clipboard"):
            loc = self._locate_clipboard(tag, anchor, n, ctx)
            if loc:
                return loc
            if mode == "clipboard":
                self._log(f"{tag} // skipped (location not verified)")
                return None
            self._log(f"{tag} // clipboard verify {BAD} -> counting")
        self._press_n(self.backend.left, n)
        self._log(f"{tag} // locate: count (unverified)")
        return _Loc("count", n)

    def _return(self, tag: str, loc: _Loc) -> None:
        b = self.backend
        if loc.kind == "uia":
            u = b.locator
            nm = getattr(u, "name", "UIA")
            snap = u.snapshot() if u else None
            if snap:
                text2, _c = snap
                after = loc.text[loc.caret:]
                target = len(text2) - len(after)
                if target >= 0 and text2[target:] == after and u.select(target, text2[:target]):
                    self._log(f"{tag} // return: {nm} {OK}")
                    return
            self._log(f"{tag} // return: {nm} {BAD} -> arrows")
        self._press_n(b.right, loc.d)

    def _apply_revision(self, r: _Rev) -> None:
        self._emit_status("revising")
        tag = f"REV {r.index + 1}/{self._nrev}"
        n = len(self._buf) - r.end            # chars between end of `first` and cursor
        # cursor is always at the end of the buffer between operations
        anchor = r.first
        if not anchor:                        # pure insertion: anchor on preceding context
            anchor = "".join(self._buf[max(0, r.start - CTX_LEN):r.start])
        ctx = "".join(self._buf[max(0, r.start - 24):r.start])
        loc = self._locate(tag, anchor, n, ctx)
        if loc is None:                       # location not verified: skip, never delete blindly
            self._pending.remove(r)
            self._emit_status("typing")
            return
        self._cur = r.end
        for _ in range(len(r.first)):
            self._wait(self.timing.backspace_delay())
            self._key_backspace()
        self._wait(self.timing.revision_think())
        self._type_run(r.revised, typos=True)
        if loc.kind == "count":
            for _ in range(n):
                self._wait(self.timing.arrow_delay())
                self._key_right()
        else:
            self._return(tag, loc)
        self._cur = len(self._buf)
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
        self._clip_verified = set()
        self._total = len(plan.text) + sum(len(r.revised) for r in plan.revisions)
        self._done = 0
        self._nrev = len(plan.revisions)
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
