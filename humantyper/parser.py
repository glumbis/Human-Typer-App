"""Parse user text into a first-pass text plus a list of pending revisions.

Syntax: ``[first][revised]`` (two bracket groups with nothing between them).
The first pass types ``first`` and skips ``revised``; later the typer goes back
and replaces ``first`` with ``revised``.

* A lone ``[x]`` (not immediately followed by ``[...]``) is literal text.
* ``\\[`` and ``\\]`` are escapes for literal brackets (also inside groups).
* No nesting: the first unescaped ``]`` closes a group.
* ``[][added]`` inserts later, ``[delete me][]`` deletes later.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Revision:
    index: int      # order of appearance in the source
    start: int      # offset of ``first`` inside Plan.text
    first: str
    revised: str

    @property
    def end(self) -> int:
        return self.start + len(self.first)


@dataclass
class Plan:
    text: str                                   # first-pass text (no revised parts)
    revisions: List[Revision] = field(default_factory=list)

    def expected(self) -> str:
        """Final text once every revision has been applied."""
        parts = []
        pos = 0
        for r in self.revisions:
            parts.append(self.text[pos:r.start])
            parts.append(r.revised)
            pos = r.end
        parts.append(self.text[pos:])
        return "".join(parts)


def _read_group(src: str, i: int) -> Optional[Tuple[str, int]]:
    """src[i] == '['. Return (content, index after closing ']') or None."""
    n = len(src)
    j = i + 1
    buf = []
    while j < n:
        c = src[j]
        if c == "\\" and j + 1 < n and src[j + 1] in "[]":
            buf.append(src[j + 1])
            j += 2
            continue
        if c == "]":
            return "".join(buf), j + 1
        buf.append(c)
        j += 1
    return None


def parse(src: str) -> Plan:
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    out: List[str] = []
    revs: List[Revision] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "\\" and i + 1 < n and src[i + 1] in "[]":
            out.append(src[i + 1])
            i += 2
            continue
        if c == "[":
            g1 = _read_group(src, i)
            if g1 is not None and g1[1] < n and src[g1[1]] == "[":
                g2 = _read_group(src, g1[1])
                if g2 is not None:
                    revs.append(Revision(len(revs), len(out), g1[0], g2[0]))
                    out.extend(g1[0])
                    i = g2[1]
                    continue
        out.append(c)
        i += 1
    return Plan("".join(out), revs)
