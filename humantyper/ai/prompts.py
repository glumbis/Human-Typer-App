"""System prompt / message construction for the text writer."""

LANGUAGES = ("auto", "english", "norsk")
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
    "norsk": "Skriv på norsk (bokmål). Write the entire text in Norwegian Bokmål.",
    "auto": "Write in the same language as the request.",
}


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
    return max(96, min(3000, int(words * 2.6) + 64))


# ---- [first][revised] pairs ------------------------------------------------
# Small local models can't be trusted to emit the bracket syntax themselves, so the app adds it:
# it picks a few sentences of the finished text, asks the model for a rougher first draft of each,
# and writes "[draft][original]". The typer then types the draft and later revises it into the
# original, so the final typed text is exactly the generated text.
import random
import re

REVISION_COUNTS = ("0", "1", "2", "3", "5", "8")
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


def pick_spans(text: str, n: int, rng=None):
    """Up to n sentence spans, spread through the text, in document order."""
    rng = rng or random.Random()
    spans = sentence_spans(text)
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


def clean_draft(draft: str, original: str):
    """Usable draft text, or None if the model's answer is unusable."""
    d = (draft or "").strip().splitlines()
    d = d[0].strip() if d else ""
    for q in ('"', "'", "“", "”", "‘", "’", "`"):
        if len(d) > 1 and d.startswith(q) and d.endswith(q):
            d = d[1:-1].strip()
    d = re.sub(r"^(draft|first draft|utkast)\s*:\s*", "", d, flags=re.I)
    if not d or any(c in d for c in "[]\\") or d.lower() == original.strip().lower():
        return None
    ratio = len(d) / max(1, len(original.strip()))
    if not 0.4 <= ratio <= 1.8:
        return None
    return d


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
