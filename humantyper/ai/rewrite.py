"""IMPROVE: rewrite the user's text with the local model, paragraph by paragraph.

Shared by the AI window and anything else that wants better text (e.g. reviewing what is in the
field the user is typing in). Prompts and cleaning are the pure functions in prompts.py."""
from typing import Callable, Optional

from . import prompts


def improve_text(engine, model_path: Optional[str], text: str, style: str = "clearer", language: str = "auto",
                 model_name: str = "", on_chunk: Optional[Callable[[str], None]] = None,
                 cancelled: Callable[[], bool] = lambda: False,
                 on_progress: Optional[Callable[[int, int, str], None]] = None,
                 stats: Optional[dict] = None) -> str:
    """Rewrite `text` in `style` (prompts.IMPROVE_STYLES), one generate call per paragraph so small
    1-4B models stay on track. Paragraph breaks are kept; a paragraph whose answer is unusable (or
    that is cut off by cancel) stays as the user wrote it.

    engine: a LocalEngine (started on model_path if given; pass None if it is already running).
    on_chunk(piece): raw streamed model output of the current paragraph (live preview only, it is
    cleaned afterwards). on_progress(done, total, text_so_far): before each paragraph and at the
    end; text_so_far is the finished text up to the paragraph being worked on. stats (dict) gets
    paragraphs / done / kept. EngineError propagates."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if model_path:
        engine.ensure_started(model_path)
    parts = prompts.split_paragraphs(text)
    todo = [i for i in range(0, len(parts), 2) if prompts.worth_improving(parts[i])]
    out, kept, done = list(parts), 0, 0
    for k, i in enumerate(todo):
        if on_progress:
            on_progress(k, len(todo), "".join(out[:i]))
        if cancelled():
            break
        par = parts[i]
        raw = []
        for piece in engine.generate(prompts.improve_messages(par, style, language, model_name),
                                     prompts.improve_max_tokens(par),
                                     temperature=prompts.improve_temperature(style)):
            raw.append(piece)
            if on_chunk:
                on_chunk(piece)
        if cancelled():                          # half an answer: keep the original paragraph
            break
        done += 1
        res = prompts.clean_improved("".join(raw), par, style)
        if res is None:
            kept += 1
        else:                                    # keep the paragraph's own indent / trailing space
            out[i] = par[:len(par) - len(par.lstrip())] + res + par[len(par.rstrip()):]
    result = "".join(out)
    if on_progress:
        on_progress(done, len(todo), result)
    if stats is not None:
        stats.update(paragraphs=len(todo), done=done, kept=kept)
    return result
