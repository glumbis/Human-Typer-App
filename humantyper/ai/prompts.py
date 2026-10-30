"""System prompt / message construction for the text writer."""

LANGUAGES = ("auto", "english", "bokmål", "nynorsk")
LEGACY_LANG = {"norsk": "bokmål"}            # setting saved by older versions
TONES = ("neutral", "casual", "formal", "student")

_TONE = {
    "neutral": "Use a natural, clear, everyday tone.",
    "casual": "Use a relaxed, conversational tone, like a person writing to a friend. Contractions are fine.",
    "formal": "Use a formal, polished and objective tone suited to academic or professional writing.",
    "student": ("Write like a capable high-school or college student: clear and sensible, mostly plain "
                "vocabulary, slightly informal in places, no grand or flowery phrasing."),
}
_LANG = {
    "english": "Write in English.",
    "bokmål": "Skriv på norsk bokmål. Write the entire text in Norwegian Bokmål.",
    "nynorsk": ("Skriv på nynorsk. Heile teksten skal vere på nynorsk, ikkje bokmål. "
                "Write the entire text in Norwegian Nynorsk (not Bokmål), with Nynorsk spelling and words "
                "such as ikkje, eg, kva, korleis, heile, vere, frå, berre, mykje."),
    "auto": "Write in the same language as the request.",
}
_LANG["norsk"] = _LANG["bokmål"]            # older saved setting


def system_prompt(words: int, language: str = "auto", tone: str = "neutral") -> str:
    return " ".join([
        "You are a writing assistant. Output ONLY the requested text, nothing else.",
        "No preamble, no title, no closing remarks, no offers of help, no explanation of what you did.",
        "Use plain paragraphs separated by a blank line. Do not use markdown: no headings, no bullet lists,",
        "no bold or italics, no asterisks, no emojis, no quotation marks around the whole text.",
        "Do not use em dashes. Vary sentence length and write the way a real person writes.",
        _TONE.get(tone, _TONE["neutral"]),
        _LANG.get(language, _LANG["auto"]),
        f"Length: about {int(words)} words.",
    ])


def uses_thinking_template(model_name: str) -> bool:
    return "qwen3" in (model_name or "").lower()


def build_messages(prompt: str, words: int, language: str = "auto", tone: str = "neutral",
                   model_name: str = ""):
    user = prompt.strip()
    if uses_thinking_template(model_name):
        user += " /no_think"
    return [{"role": "system", "content": system_prompt(words, language, tone)},
            {"role": "user", "content": user}]


def max_tokens_for(words: int) -> int:
    return max(96, min(10000, int(words * 2.6) + 64))


# ---- [first][revised] pairs ------------------------------------------------
# Small local models can't be trusted to emit the bracket syntax themselves, so the app adds it:
# it picks a few sentences of the finished text, asks the model for a rougher first draft of each,
# and writes "[draft][original]". The typer then types the draft and later revises it into the
# original, so the final typed text is exactly the generated text.
import random
import re

REVISION_COUNTS = ("0", "1", "3", "5", "8")
_SENT = re.compile(r"[^.!?\n]+[.!?]+[\"')]*")


def escape_brackets(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def sentence_spans(text: str):
    """(start, end) of sentences worth revising: 5+ words, no brackets or backslashes."""
    spans = []
    for m in _SENT.finditer(text):
        s = m.group(0)
        lead = len(s) - len(s.lstrip())
        a, b = m.start() + lead, m.end()
        body = text[a:b]
        if len(body.split()) >= 5 and not any(c in body for c in "[]\\"):
            spans.append((a, b))
    return spans


def clause_spans(text: str):
    """Finer candidates for when more revisions are wanted than there are sentences: sentences
    split at commas/semicolons into parts of 4+ words."""
    out = []
    for a, b in sentence_spans(text):
        start = a
        for m in re.finditer(r"[,;:] ", text[a:b]):
            end = a + m.start() + 1
            if len(text[start:end].split()) >= 4:
                out.append((start, end))
            start = a + m.end()
        if len(text[start:b].split()) >= 4:
            out.append((start, b))
    return out


def chunk_spans(spans, text: str, size: int = 5):
    """Finest candidates: each span cut into runs of `size` words (the tail joins the last run)."""
    out = []
    for a, b in spans:
        words = list(re.finditer(r"\S+", text[a:b]))
        if len(words) < 2 * 4:
            out.append((a, b))
            continue
        k = max(1, len(words) // size)
        bounds = [round(i * len(words) / k) for i in range(k + 1)]
        for i in range(k):
            out.append((a + words[bounds[i]].start(), a + words[bounds[i + 1] - 1].end()))
    return out


def pick_spans(text: str, n: int, rng=None):
    """Up to n sentence spans (clause spans if n exceeds the sentences), spread through the text,
    in document order."""
    rng = rng or random.Random()
    spans = sentence_spans(text)
    if n > len(spans):
        spans = max(spans, clause_spans(text), key=len)
    if n > len(spans):
        spans = max(spans, chunk_spans(spans, text), key=len)
    if n <= 0 or not spans:
        return []
    if n >= len(spans):
        return spans
    step = len(spans) / float(n)
    picks = []
    for i in range(n):
        lo, hi = int(i * step), max(int(i * step) + 1, int((i + 1) * step))
        picks.append(spans[rng.randrange(lo, min(hi, len(spans)))])
    return sorted(set(picks))


def draft_messages(sentence: str, language: str = "auto", model_name: str = ""):
    system = " ".join([
        "You turn one finished sentence into the rougher first draft a person might have typed before",
        "improving it. Keep the same meaning and a similar length, but use plainer, clumsier or less",
        "precise wording (a weaker word choice, a missing detail, or an awkward order).",
        "Output ONLY the draft sentence: no quotes, no brackets, no explanation.",
        _LANG.get(language, "Write in the same language as the sentence."),
    ])
    user = sentence.strip()
    if uses_thinking_template(model_name):
        user += " /no_think"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _letters(s: str) -> str:
    """Only the letters/digits, lower-cased: two texts with the same words compare equal."""
    return re.sub(r"[\W_]+", "", s.lower())


_PREAMBLE = re.compile(r"^(here('s| is)|sure|okay|ok|her er|her kjem|klart|sjølvsagt|selvfølgelig)\b.*:\s*$",
                       re.I)


def clean_draft(draft: str, original: str):
    """Usable draft text, or None if the model's answer is unusable."""
    lines = [ln.strip() for ln in (draft or "").strip().splitlines() if ln.strip()]
    if lines and (_PREAMBLE.match(lines[0]) or lines[0].endswith(":")):
        lines = lines[1:]                       # "Here is the draft:" / "Her er utkastet:" lead-in
    d = lines[0] if lines else ""
    for q in ('"', "'", "“", "”", "‘", "’", "`", "«", "»"):
        if len(d) > 1 and d.startswith(q) and d.endswith(q):
            d = d[1:-1].strip()
    d = re.sub(r"^(draft|first draft|utkast|førsteutkast|fyrsteutkast)\s*:\s*", "", d, flags=re.I)
    # the same words with other punctuation/case would be a pointless revision
    if not d or any(c in d for c in "[]\\") or _letters(d) == _letters(original):
        return None
    ratio = len(d) / max(1, len(original.strip()))
    if not 0.4 <= ratio <= 1.8:
        return None
    return d


_CUT_WORDS = {"and", "but", "because", "which", "while", "so", "that", "with", "when", "since",
              "og", "men", "fordi", "som", "mens", "så", "med", "når", "siden",
              "medan", "sidan", "då", "slik"}                       # nynorsk
# small words a cut-short draft should not end on
_WEAK_END = {"a", "an", "the", "to", "of", "for", "and", "or", "is", "are", "was", "at", "in", "on", "with", "that",
             "å", "er", "var", "at", "og", "eller", "for", "til", "i", "på", "med", "som", "en", "ei", "et", "eit",
             "ein", "av", "om", "det", "den", "dei", "de", "seg", "sin", "si", "sitt"}


def fallback_draft(original: str, rng=None):
    """Model-free draft: the sentence cut short at a natural joint (comma or a word like 'and'),
    so the later revision 'adds the rest'. None if the sentence is too short to cut."""
    rng = rng or random.Random()
    s = original.strip()
    words = s.split()
    if len(words) < 6:
        return None
    end_punct = s[-1] if s[-1] in ".!?" else ""          # mid-sentence chunks stay unpunctuated

    def cut(i):
        while i > 3 and words[i - 1].lower().strip(",;:") in _WEAK_END:
            i -= 1                                      # never end on "er", "at", "og", "the", ...
        return " ".join(words[:i]).rstrip(",;:") + end_punct

    def ok(i):
        return 0.45 <= len(cut(i)) / float(len(s)) <= 0.85

    joints = [i for i in range(3, len(words) - 1)
              if (words[i - 1].endswith(",") or words[i].lower().strip(",;") in _CUT_WORDS) and ok(i)]
    if joints:
        i = rng.choice(joints)
    else:                                               # no good joint: cut at roughly 60-75 %
        plain = [i for i in range(3, len(words) - 1) if ok(i)]
        if not plain:
            return None
        target = len(words) * rng.uniform(0.6, 0.75)
        i = min(plain, key=lambda k: abs(k - target))
    return clean_draft(cut(i), s)


def apply_revisions(text: str, pairs) -> str:
    """pairs: [(start, end, draft)] in document order -> text with [draft][original] inserted and
    every other bracket escaped so it is typed literally."""
    out, pos = [], 0
    for a, b, draft in sorted(pairs):
        out.append(escape_brackets(text[pos:a]))
        out.append(f"[{draft}][{text[a:b]}]")
        pos = b
    out.append(escape_brackets(text[pos:]))
    return "".join(out)
