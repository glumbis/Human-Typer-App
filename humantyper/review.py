"""REVIEW FIELD: read the text the user wrote in another app, let the local AI improve it, and turn
the difference into edits the typing engine applies in place (TypingEngine.run_edit).

Pure helpers (compute_edits / apply_edits / improved_spans) and the field grab (grab_field) work on
any backend with the documented primitives, so FakeBackend covers them in tests."""
import difflib
import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .locator import normalize

MAX_CHARS = 6000        # longest field text REVIEW accepts (model time + arrow-key distances)
COPY_WAIT = 1.0         # seconds to wait for the clipboard to change after Ctrl/Cmd+C
MERGE_WORDS = 2         # changes this few words apart become one edit (one think pause, not two)

Edit = Tuple[int, int, str]     # (start, end, replacement): original[start:end] -> replacement

_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")


def _tokens(s: str) -> List[str]:
    return _TOKEN.findall(s)


def _is_word(tok: str) -> bool:
    return any(c.isalnum() for c in tok)


def _offsets(toks: List[str]) -> List[int]:
    out, pos = [0], 0
    for t in toks:
        pos += len(t)
        out.append(pos)
    return out


def compute_edits(original: str, improved: str, merge_words: int = MERGE_WORDS) -> List[Edit]:
    """Changed spans between `original` and `improved` as [(start, end, replacement)] in document
    order, non-overlapping, in `original` coordinates; apply_edits(original, result) == improved.

    Diffed on word / whitespace / punctuation tokens (a changed word is retyped whole, like a
    person would); changes at most `merge_words` words apart on the same line are grouped into one
    edit. Every edit's original text contains a word (pure insertions and punctuation-only changes
    are widened to the neighbouring word), so the typer always has a real phrase to locate."""
    if original == improved:
        return []
    ot, it = _tokens(original), _tokens(improved)
    oo, io = _offsets(ot), _offsets(it)
    sm = difflib.SequenceMatcher(None, ot, it, autojunk=False)
    groups: List[List[int]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if groups:
            p = groups[-1]
            gap = ot[p[1]:i1]
            if sum(map(_is_word, gap)) <= merge_words and not any("\n" in t for t in gap):
                p[1], p[3] = i2, j2
                continue
        groups.append([i1, i2, j1, j2])
    # widen edits without a word in their original text (tokens next to a group are equal ones,
    # so i and j move together); widening may run into the previous group: merged below
    for g in groups:
        while not any(_is_word(t) for t in ot[g[0]:g[1]]) and g[0] > 0:
            g[0] -= 1
            g[2] -= 1
        while not any(_is_word(t) for t in ot[g[0]:g[1]]) and g[1] < len(ot):
            g[1] += 1
            g[3] += 1
    merged: List[List[int]] = []
    for g in groups:
        if merged and g[0] <= merged[-1][1]:
            p = merged[-1]
            p[1], p[3] = max(p[1], g[1]), max(p[3], g[3])
        else:
            merged.append(list(g))
    return [(oo[i1], oo[i2], improved[io[j1]:io[j2]]) for i1, i2, j1, j2 in merged]


def apply_edits(original: str, edits: List[Edit]) -> str:
    out, pos = [], 0
    for s, e, rep in sorted(edits):
        out.append(original[pos:s])
        out.append(rep)
        pos = e
    out.append(original[pos:])
    return "".join(out)


def improved_spans(edits: List[Edit]) -> List[Tuple[int, int]]:
    """Where each edit's replacement sits in the improved text (for highlighting)."""
    out, delta = [], 0
    for s, e, rep in sorted(edits):
        a = s + delta
        out.append((a, a + len(rep)))
        delta += len(rep) - (e - s)
    return out


# ---- reading the field ----------------------------------------------------------------------
@dataclass
class Grab:
    text: str = ""
    method: str = ""        # "UIA" / "AX" / "clipboard"
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


NOTHING_COPIED = ("Could not read the text field: nothing was copied. Click into the text you want "
                  "reviewed first (some apps do not allow copying).")
CLIP_NEEDS = ("select_all", "copy", "caret_to_end", "clipboard_seq", "clipboard_get_text",
              "clipboard_snapshot", "clipboard_restore")


def _check_text(text: str, method: str, max_chars: int) -> Grab:
    if not text.strip():
        return Grab("", method, "The field looks empty - write something there first.")
    if len(text) > max_chars:
        return Grab("", method, f"The text is too long to review ({len(text)} characters, max {max_chars}). "
                                "Review a shorter text.")
    return Grab(text, method)


def grab_field(backend, sleep=time.sleep, max_chars: int = MAX_CHARS, use_locator: bool = True,
               copy_wait: float = COPY_WAIT) -> Grab:
    """Read the focused field's whole text and leave the caret at its END (where
    TypingEngine.run_edit expects it). Accessibility first (UI Automation / AX: reads without
    touching the clipboard), else select all + copy, with the user's clipboard saved and restored
    and the copy verified through clipboard_seq; the selection is always collapsed again
    (caret_to_end) so no later keystroke can replace the user's text."""
    loc = getattr(backend, "locator", None) if use_locator else None
    if loc is not None:
        snap = None
        try:
            snap = loc.snapshot()
        except Exception:
            snap = None
        if snap and snap[0] and snap[0].strip():
            text = normalize(snap[0])
            if hasattr(backend, "caret_to_end"):
                backend.caret_to_end()
            return _check_text(text, getattr(loc, "name", "UIA"), max_chars)
    if not all(hasattr(backend, a) for a in CLIP_NEEDS):
        return Grab("", "clipboard", "Reading the field is not supported on this system.")
    snap = backend.clipboard_snapshot()
    if snap is None:
        return Grab("", "clipboard", "Could not save your clipboard, so the field was not read.")
    text: Optional[str] = None
    try:
        seq0 = backend.clipboard_seq()
        backend.select_all()
        sleep(0.05)
        backend.copy()
        waited = 0.0
        while True:
            if backend.clipboard_seq() != seq0:
                text = backend.clipboard_get_text() or ""
                break
            if waited >= copy_wait:
                break
            sleep(0.05)
            waited += 0.05
    finally:
        try:
            backend.caret_to_end()          # collapse the selection, caret at the end of the text
        except Exception:
            pass
        try:
            backend.clipboard_restore(snap)
        except Exception:
            pass
    if text is None:
        return Grab("", "clipboard", NOTHING_COPIED)
    return _check_text(normalize(text), "clipboard", max_chars)


# ---- AI model for REVIEW (same choice as the AI window) ---------------------------------------
def resolve_model(cfg: dict, models=None):
    """(path, name) of the model the AI window would use: the saved one if it still exists, else
    the default pick; (None, "") without any model."""
    from .ai import models as mdl
    models = mdl.discover() if models is None else models
    want = (cfg or {}).get("model") or ""
    for m in models:
        if want and os.path.normcase(m.path) == os.path.normcase(want):
            return m.path, m.name
    d = mdl.pick_default(models)
    return (d.path, d.name) if d else (None, "")
