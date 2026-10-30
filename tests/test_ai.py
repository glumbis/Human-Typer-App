import hashlib
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from humantyper import plat
from humantyper.ai import engine as E
from humantyper.ai import models as M
from humantyper.ai import prompts


def _touch(path, size=2_000_000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)


# ---- discovery / catalog ------------------------------------------------------
def test_discover_skips_mmproj_and_small(tmp_path):
    _touch(str(tmp_path / "lm" / "gemma-4-E2B-it-GGUF" / "gemma-4-E2B-it-Q4_K_M.gguf"))
    _touch(str(tmp_path / "lm" / "gemma-4-E2B-it-GGUF" / "mmproj-gemma-4-E2B-it-BF16.gguf"))
    _touch(str(tmp_path / "lm" / "x" / "tiny.gguf"), 100)
    _touch(str(tmp_path / "lm" / "x" / "notes.txt"))
    found = M.discover([str(tmp_path / "lm"), str(tmp_path / "missing")])
    assert [m.name for m in found] == ["gemma-4-E2B-it - Q4_K_M"]
    assert found[0].source == "external" and not found[0].deletable


def test_app_dir_models_are_deletable_external_never(tmp_path, monkeypatch):
    monkeypatch.setattr(plat, "data_dir", lambda: str(tmp_path / "HumanTyper"))
    app = M.app_models_dir()
    _touch(os.path.join(app, "Qwen3-1.7B-Q4_K_M.gguf"))
    _touch(str(tmp_path / "lm" / "other-Q4_K_M.gguf"))
    found = {m.name: m for m in M.discover([str(tmp_path / "lm"), app])}
    own, ext = found["Qwen3-1.7B - Q4_K_M"], found["other - Q4_K_M"]
    assert own.deletable and not ext.deletable
    assert M.delete_model(ext) is False and os.path.exists(ext.path)
    assert M.delete_model(own) is True and not os.path.exists(own.path)


def test_default_and_catalog_matching(tmp_path):
    _touch(str(tmp_path / "a" / "gemma-4-E2B-it-Q4_K_M.gguf"), 3_000_000)
    _touch(str(tmp_path / "a" / "Llama-3.2-1B-Instruct-Q4_K_M.gguf"), 2_000_000)
    found = M.discover([str(tmp_path / "a")])
    assert "gemma-4-e2b" in M.pick_default(found).name.lower()
    only_small = [m for m in found if "Llama" in m.name]
    assert M.pick_default(only_small).name.startswith("Llama")
    assert M.pick_default([]) is None
    by_id = {e.id: e for e in M.CATALOG}
    assert M.installed_path(by_id["gemma4-e2b"], found) is not None
    assert M.installed_path(by_id["qwen3-1.7b"], found) is None
    assert all(e.url.startswith("https://huggingface.co/") and e.url.endswith(".gguf") for e in M.CATALOG)
    assert all(re.fullmatch(r"[0-9a-f]{64}", e.sha256) and e.size > 0 for e in M.CATALOG)


def test_friendly_name_and_size():
    assert M.friendly_name("Llama-3.2-1B-Instruct-Q4_K_M.gguf") == "Llama-3.2-1B-Instruct - Q4_K_M"
    assert M.friendly_name("weird.gguf") == "weird"
    assert M.human_size(3427880384) == "3.2 GB"


# ---- downloader against a local Range-capable server ----------------------------
DATA = os.urandom(3_000_000 + 123)


class _H(BaseHTTPRequestHandler):
    ignore_range = False
    log = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        _H.log.append(rng)
        start = 0
        if rng and not _H.ignore_range:
            start = int(re.match(r"bytes=(\d+)-", rng).group(1))
            if start >= len(DATA):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(DATA)}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        else:
            self.send_response(200)
        body = DATA[start:] if not (_H.ignore_range) else DATA
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server():
    _H.ignore_range = False
    _H.log = []
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/f.gguf"
    srv.shutdown()


def test_download_full_with_sha(server, tmp_path):
    dest = str(tmp_path / "m" / "f.gguf")
    seen = []
    M.download_file(server, dest, len(DATA), hashlib.sha256(DATA).hexdigest(),
                    progress=lambda d, t, s: seen.append((d, t)))
    assert open(dest, "rb").read() == DATA and not os.path.exists(dest + ".part")
    assert seen[-1] == (len(DATA), len(DATA))


def test_download_resume_uses_range(server, tmp_path):
    dest = str(tmp_path / "f.gguf")
    with open(dest + ".part", "wb") as f:
        f.write(DATA[:1_000_000])
    M.download_file(server, dest, len(DATA), hashlib.sha256(DATA).hexdigest())
    assert _H.log == ["bytes=1000000-"]
    assert open(dest, "rb").read() == DATA


def test_download_server_ignores_range_restarts(server, tmp_path):
    _H.ignore_range = True
    dest = str(tmp_path / "f.gguf")
    with open(dest + ".part", "wb") as f:
        f.write(b"garbage" * 1000)
    M.download_file(server, dest, len(DATA))
    assert open(dest, "rb").read() == DATA


def test_download_cancel_keeps_part_then_resumes(server, tmp_path):
    dest = str(tmp_path / "f.gguf")
    ev = threading.Event()
    ev.set()
    with pytest.raises(M.DownloadCancelled):
        M.download_file(server, dest, len(DATA), cancel=ev, chunk=100_000)
    assert not os.path.exists(dest)
    M.download_file(server, dest, len(DATA))
    assert open(dest, "rb").read() == DATA


def test_download_bad_checksum_and_size(server, tmp_path):
    dest = str(tmp_path / "f.gguf")
    with pytest.raises(M.DownloadError):
        M.download_file(server, dest, len(DATA), "0" * 64)
    assert not os.path.exists(dest) and not os.path.exists(dest + ".part")
    with pytest.raises(M.DownloadError):
        M.download_file(server, dest, len(DATA) + 5)


# ---- SSE / think stripping ------------------------------------------------------
def _sse(*deltas, timings=None):
    lines = []
    for d in deltas:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": d}}]}))
        lines.append("")
    if timings:
        lines.append("data: " + json.dumps({"choices": [], "timings": timings}))
    lines.append("data: [DONE]")
    return lines


def test_sse_text_basic_and_timings():
    t = {}
    out = "".join(E.sse_text(_sse("Hel", "lo ", "world", timings={"predicted_per_second": 9.5}),
                            on_timings=t.update))
    assert out == "Hello world" and t["predicted_per_second"] == 9.5


def test_sse_ignores_garbage_and_bytes():
    lines = [b": keepalive", b"data: not json", b'data: {"choices":[{"delta":{"content":"ok"}}]}', b"data: [DONE]",
             b'data: {"choices":[{"delta":{"content":"after"}}]}']
    assert "".join(E.sse_text(lines)) == "ok"


@pytest.mark.parametrize("chunks", [
    ["<think>reasoning</think>\n\nAnswer"],
    ["<thi", "nk>reas", "oning</th", "ink>", "\n\nAns", "wer"],
    ["<think>", "a</think>Answer"],
    list("<think>x y</think>Answer"),
])
def test_think_stripped_across_chunk_boundaries(chunks):
    assert "".join(E.sse_text(_sse(*chunks))) == "Answer"


def test_think_not_confused_with_lt_text():
    assert "".join(E.sse_text(_sse("a < b and <b", "> c"))) == "a < b and <b> c"


# ---- engine command line + GPU->CPU fallback ----------------------------------------
class FakeProc:
    def __init__(self, die_code=None):
        self.die_code, self.returncode, self.terminated = die_code, die_code, False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = -9


@pytest.fixture()
def model(tmp_path):
    p = tmp_path / "m.gguf"
    p.write_bytes(b"x")
    return str(p)


@pytest.fixture(autouse=True)
def _log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(plat, "data_dir", lambda: str(tmp_path / "la" / "HumanTyper"))


def make_engine(popen, health="ok", settings=None, **kw):
    saved = []
    e = E.LocalEngine(exe="C:/x/" + E.exe_name(), settings=settings if settings is not None else {},
                      popen=popen, health=lambda port: health(port) if callable(health) else health,
                      port_fn=lambda: 5555, use_job=False, start_timeout=1.0,
                      save_settings=lambda d: saved.append(dict(d)), **kw)
    e.available = lambda: True
    return e, saved


def test_build_cmd():
    e, _ = make_engine(None)
    cmd = e.build_cmd("C:/m.gguf", 1234, 99)
    assert cmd[0].endswith(E.exe_name())
    for a, b in [("--host", "127.0.0.1"), ("--port", "1234"), ("-m", "C:/m.gguf"), ("-c", "12288"), ("-ngl", "99")]:
        assert cmd[cmd.index(a) + 1] == b
    assert "--jinja" in cmd


def test_gpu_start_ok(model):
    cmds = []

    def popen(cmd, **kw):
        cmds.append(cmd)
        assert kw.get("stdin") is not None
        return FakeProc()

    e, saved = make_engine(popen)
    assert e.ensure_started(model) == "GPU"
    assert cmds[0][cmds[0].index("-ngl") + 1] == "99" and not saved
    assert e.ensure_started(model) == "GPU" and len(cmds) == 1      # reused
    e.stop()


def test_gpu_crash_falls_back_to_cpu_and_remembers(model):
    cmds = []

    def popen(cmd, **kw):
        cmds.append(cmd)
        return FakeProc(die_code=3 if cmd[cmd.index("-ngl") + 1] == "99" else None)

    e, saved = make_engine(popen)
    assert e.ensure_started(model) == "CPU"
    assert [c[c.index("-ngl") + 1] for c in cmds] == ["99", "0"]
    assert e.settings["force_cpu"] is True and saved and saved[-1]["force_cpu"] is True
    e.stop()
    cmds.clear()
    assert e.ensure_started(model) == "CPU" and [c[c.index("-ngl") + 1] for c in cmds] == ["0"]   # CPU straight away
    e.stop()


def test_gpu_health_timeout_falls_back(model):
    cmds = []

    def popen(cmd, **kw):
        cmds.append(cmd)
        return FakeProc()

    e, _ = make_engine(popen, health=lambda port: "ok" if len(cmds) == 2 else "loading")
    e.start_timeout = 0.5
    assert e.ensure_started(model) == "CPU"


def test_both_fail_raises(model):
    e, _ = make_engine(lambda cmd, **kw: FakeProc(die_code=1))
    with pytest.raises(E.EngineError) as ei:
        e.ensure_started(model)
    assert ei.value.kind == "start"


def test_missing_engine_and_model(tmp_path):
    e = E.LocalEngine(exe=str(tmp_path / "nope.exe"), settings={})
    with pytest.raises(E.EngineError) as ei:
        e.ensure_started("x.gguf")
    assert ei.value.kind == "missing" and "fetch" in str(ei.value).lower()


def test_switching_model_restarts(model, tmp_path):
    other = tmp_path / "o.gguf"
    other.write_bytes(b"x")
    procs = []

    def popen(cmd, **kw):
        procs.append(FakeProc())
        return procs[-1]

    e, _ = make_engine(popen)
    e.ensure_started(model)
    e.ensure_started(str(other))
    assert len(procs) == 2 and procs[0].terminated


def test_oom_detection():
    assert E._looks_oom("ggml_vulkan: Device memory allocation failed ... ErrorOutOfDeviceMemory")
    assert E._looks_oom("std::bad_alloc")
    assert not E._looks_oom("all good")


def test_generate_stream_against_fake_server(model):
    """Full HTTP path: the engine posts to a local SSE server and yields stripped text."""
    seen = {}

    class S(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers["Content-Length"])
            seen["body"] = json.loads(self.rfile.read(n))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for line in _sse("<think>hm</think>Hel", "lo", timings={"predicted_per_second": 7.25}):
                self.wfile.write((line + "\n").encode())

    srv = HTTPServer(("127.0.0.1", 0), S)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        e, _ = make_engine(lambda cmd, **kw: FakeProc())
        e._port_fn = lambda: srv.server_address[1]
        e.ensure_started(model)
        out = "".join(e.generate(prompts.build_messages("hi", 50, "english", "casual", "Qwen3-1.7B"), 100))
        assert out == "Hello" and e.last_tps == 7.25
        b = seen["body"]
        assert b["stream"] is True and b["chat_template_kwargs"] == {"enable_thinking": False}
        assert b["messages"][-1]["content"].endswith("/no_think")
        e.stop()
    finally:
        srv.shutdown()


def test_prompt_builder():
    m = prompts.build_messages("about cats", 120, "norsk", "formal", "gemma")
    assert m[0]["role"] == "system" and "120 words" in m[0]["content"] and "Norwegian" in m[0]["content"]
    assert m[1]["content"] == "about cats"
    assert 96 <= prompts.max_tokens_for(50) < prompts.max_tokens_for(800) <= 10000


# ---- [draft][final] revision pairs ------------------------------------------
from humantyper.ai import prompts as P


def test_apply_revisions_types_back_to_original():
    import random
    from humantyper.backends import FakeBackend
    from humantyper.engine import Settings, TypingEngine
    text = ("Cooking teaches students useful skills for life. It also saves money every week. "
            "Some recipes use [brackets] in notes. Sharing a meal with friends builds community.")
    spans = P.pick_spans(text, 3, random.Random(1))
    assert spans and all("[" not in text[a:b] for a, b in spans)
    pairs = [(a, b, "rough " + text[a:b].split()[0].lower() + " draft words here") for a, b in spans]
    marked = P.apply_revisions(text, pairs)
    assert marked.count("][") == len(pairs)
    be = FakeBackend()
    TypingEngine(be, Settings(typo_rate=0.0, seed=3, locate_mode="count"), sleep=lambda s: None).run(marked)
    assert be.text == text


def test_clean_draft_rejects_bad_answers():
    orig = "Cooking teaches students useful skills for life."
    assert P.clean_draft('"Cooking teaches kids some skills."', orig) == "Cooking teaches kids some skills."
    assert P.clean_draft(orig, orig) is None
    assert P.clean_draft("[x][y]", orig) is None
    assert P.clean_draft("", orig) is None
    assert P.clean_draft("Draft: Cooking is good for students to learn.", orig) == "Cooking is good for students to learn."
    assert P.clean_draft("ok", orig) is None


def test_fallback_draft_cuts_at_a_joint():
    import random
    orig = "Cooking teaches you about nutrition and how different ingredients work together."
    d = P.fallback_draft(orig, random.Random(0))
    assert d and d.endswith(".") and orig.startswith(d[:-1]) and len(d) < len(orig)
    assert P.fallback_draft("Too short to cut.") is None


def test_pick_spans_uses_clauses_when_more_revisions_than_sentences():
    import random
    text = ("Cooking is useful for students, it saves money every week, and it builds real confidence. "
            "Sharing meals with friends is fun, and it brings people closer together.")
    assert len(P.sentence_spans(text)) == 2
    spans = P.pick_spans(text, 5, random.Random(2))
    assert len(spans) > 2
    assert all(text[a:b].strip() and "[" not in text[a:b] for a, b in spans)
    marked = P.apply_revisions(text, [(a, b, "x " + text[a:b].split()[0]) for a, b in spans])
    from humantyper.backends import FakeBackend
    from humantyper.engine import Settings, TypingEngine
    be = FakeBackend()
    TypingEngine(be, Settings(typo_rate=0.0, seed=1, locate_mode="count"), sleep=lambda s: None).run(marked)
    assert be.text == text


def test_languages_bokmal_nynorsk_and_legacy():
    assert prompts.LANGUAGES == ("auto", "english", "bokmål", "nynorsk")
    nn = prompts.build_messages("om katter", 100, "nynorsk", "neutral", "borealis")[0]["content"]
    assert "nynorsk" in nn.lower() and "ikkje bokmål" in nn
    nb = prompts.build_messages("om katter", 100, "bokmål", "neutral", "borealis")[0]["content"]
    assert "Bokmål" in nb and "nynorsk" not in nb.lower()
    # a 'norsk' setting saved by older versions still means bokmål
    assert prompts.LEGACY_LANG["norsk"] == "bokmål"
    assert prompts.build_messages("x", 50, "norsk")[0]["content"] == prompts.build_messages("x", 50, "bokmål")[0]["content"]
    assert "Nynorsk" in prompts.draft_messages("Eg likar å lage mat.", "nynorsk")[0]["content"]


def test_borealis_in_catalog():
    e = next(x for x in M.CATALOG if x.id == "borealis-4b")
    assert e.repo == "NbAiLab/borealis-4b-gguf" and e.filename == "borealis-4b-Q4_K_M.gguf"
    assert e.url == "https://huggingface.co/NbAiLab/borealis-4b-gguf/resolve/main/borealis-4b-Q4_K_M.gguf"
    assert e.size == 2489894560


def test_clean_draft_rejects_punctuation_only_and_preamble():
    orig = "På den måten bidrar matlaging til allsidig utvikling og er en verdifull del av skolegangen."
    assert P.clean_draft(orig.rstrip("."), orig) is None                       # only the full stop differs
    assert P.clean_draft(orig.upper(), orig) is None
    assert P.clean_draft("Her er utkastet:\nMatlaging er bra for utvikling og skolen.", orig) == \
        "Matlaging er bra for utvikling og skolen."
    assert P.clean_draft("Here is the draft:\n\"Cooking is good for kids.\"", "Cooking is very good for all kids.") == \
        "Cooking is good for kids."


def test_fallback_draft_nynorsk_long_sentences():
    import random
    sents = ["For det første lærer dei om ernæring, og dei får vite kvifor ulike matvarer er gode eller dårlege for helsa.",
             "Å lære å lage mat er viktig for elevar av fleire grunnar.",
             "Dei lærer òg at sunn mat er nødvendig for å ha nok energi.",
             "Til slutt får dei eit verktøy for livet, og det gjer det enklare å lage mat for seg sjølv, familie og venner."]
    for s in sents:
        for seed in range(5):
            d = P.fallback_draft(s, random.Random(seed))
            assert d, s
            assert 0.4 <= len(d) / len(s) <= 0.9
            last = d.rstrip(".!?").split()[-1].lower()
            assert last not in {"er", "at", "og", "å", "for", "i", "til", "om", "av"}, d
