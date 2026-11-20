"""IMPROVE mode: prompts, cleaning, paragraph-wise rewriting and [original][improved] pairs."""
import random

from humantyper.ai import prompts as P
from humantyper.ai import rewrite
from humantyper.ai import settings as S
from humantyper.parser import parse


# ---- prompt ------------------------------------------------------------------
def test_improve_messages_styles_language_and_no_think():
    assert P.IMPROVE_STYLES == ("fix", "clearer", "formal", "casual", "shorter")
    m = P.improve_messages("  I has a cat.  ", "fix", "auto", "gemma")
    sys_ = m[0]["content"]
    assert m[0]["role"] == "system" and m[1] == {"role": "user", "content": "I has a cat."}
    assert "ONLY the rewritten text" in sys_ and "spelling" in sys_ and "Never translate" in sys_
    assert P.improve_messages("x y z", "clearer", "auto", "Qwen3-1.7B - Q4_K_M")[1]["content"].endswith("/no_think")
    assert "Nynorsk" in P.improve_messages("x", "formal", "nynorsk")[0]["content"]
    assert "Never translate" not in P.improve_messages("x", "formal", "english")[0]["content"]
    for st in P.IMPROVE_STYLES:                     # every style has its own instruction + hint
        assert P.improve_system_prompt(st) != P.improve_system_prompt("fix" if st != "fix" else "clearer")
        assert P.IMPROVE_HINTS[st]
    assert P.improve_max_tokens("word " * 200) > P.improve_max_tokens("word " * 20) >= 96
    assert P.improve_temperature("fix") < P.improve_temperature("clearer")


# ---- cleaning ----------------------------------------------------------------
def test_clean_improved_strips_lead_ins_notes_and_quotes():
    orig = "I has a cat and it are very nice to me."
    good = "I have a cat and it is very nice to me."
    assert P.clean_improved(good, orig, "fix") == good
    assert P.clean_improved(f"Here is the improved text:\n\n\"{good}\"", orig) == good
    assert P.clean_improved(f"Sure! Here's a clearer version: {good}", orig) == good
    assert P.clean_improved(f"Corrected text:\n{good}\n\nNote: I fixed the verbs.", orig, "fix") == good
    assert P.clean_improved(f"Her er den forbedra teksten:\n{good}", orig) == good
    assert P.clean_improved(f"**{good}**", orig) == good
    assert P.clean_improved(f"{good}\n\nI changed 'has' to 'have'.", orig) == good


def test_clean_improved_rejects_unusable_answers():
    orig = "Cooking teaches students useful skills, and it saves them money every single week."
    assert P.clean_improved("", orig) is None
    assert P.clean_improved("   \n ", orig) is None
    assert P.clean_improved("Cooking.", orig) is None                       # far too short
    assert P.clean_improved(orig + " " + orig + " " + orig, orig) is None    # far too long
    # 'shorter' may cut a lot, 'fix' may not
    short = "Cooking teaches useful skills and saves money."
    assert P.clean_improved(short, orig, "shorter") == short
    assert P.clean_improved(short, orig, "fix") is None


def test_clean_improved_keeps_lines_and_one_paragraph():
    orig = "Dear Sir,\nI wants to complain about the noise."
    assert P.clean_improved("Dear Sir,\nI want to complain about the noise.", orig) == \
        "Dear Sir,\nI want to complain about the noise."
    # a single-paragraph input never gets a blank line from the model
    orig = "This is one paragraph. It has two sentences in it."
    assert P.clean_improved("This is one paragraph.\n\nIt has two sentences.", orig) == \
        "This is one paragraph. It has two sentences."
    # an original that itself starts with a lead-in line is left alone
    orig = "Here is my plan:\nWe goes to the shop and buys bread."
    assert P.clean_improved("Here is my plan:\nWe go to the shop and buy bread.", orig).startswith("Here is my plan:")


def test_clean_draft_still_uses_shared_cleaning():
    assert P.clean_draft("Sure, here you go:\n“Cooking is good for kids.”", "Cooking is very good for all kids.") == \
        "Cooking is good for kids."


def test_split_paragraphs_round_trip():
    t = "One two three.\n\nFour five six.\n  \n\nSeven.\nEight nine ten."
    parts = P.split_paragraphs(t)
    assert "".join(parts) == t and len(parts) == 5
    assert parts[0::2] == ["One two three.", "Four five six.", "Seven.\nEight nine ten."]
    assert P.worth_improving("One two three.") and not P.worth_improving("Introduction") \
        and not P.worth_improving("--- --- ---")


# ---- [original][improved] pairs ---------------------------------------------------
def _check(original, improved):
    marked, k = P.build_revision_text(original, improved)
    plan = parse(marked)
    assert plan.expected() == improved                  # what ends up typed is the improved text
    return marked, k, plan


def test_revision_pairs_sentence_by_sentence():
    o = "I has a cat. It are very nice. We goes to the park.\n\nThe second para is here. Short one."
    i = "I have a cat. It is very nice. We go to the park.\n\nThe second paragraph is here. Short one."
    marked, k, plan = _check(o, i)
    assert k == 4 and len(plan.revisions) == 4
    assert plan.text == o                               # the first pass types the user's own wording
    assert "[I has a cat.][I have a cat.]" in marked and "] Short one." in marked
    assert [(r.first, r.revised) for r in plan.revisions][1] == ("It are very nice.", "It is very nice.")


def test_revision_pairs_deleted_added_merged_sentences():
    cases = [("A one two. B three four. C five six.", "A one two. C five six."),           # dropped
             ("A one two. B three four. C five six.", "A one two. B three four."),         # dropped at end
             ("A one two. C five six.", "A one two. B new one. C five six."),              # added
             ("A one two. C five six.", "A one two. C five six. D added here."),           # added at end
             ("We went home, and then we ate. It was late.", "We went home. Then we ate. It was very late."),
             ("Dette er ein test. Eg likar mat.", "Dette er ein test. Eg liker god mat.")]
    for o, i in cases:
        marked, k, plan = _check(o, i)
        assert k >= 1 and plan.text == o, (o, i, marked)


def test_revision_pairs_escape_brackets_and_fall_back():
    o = "Use the [x] key now. Then press enter."
    i = "Press the [x] key now. Then press enter."
    marked, k, plan = _check(o, i)
    assert k == 1 and "\\[x\\]" in marked and plan.text == o
    # nothing changed / paragraph count changed -> improved text unchanged, no pairs
    assert P.build_revision_text(o, o) == (o, 0)
    assert P.build_revision_text("One two. Three four.", "One two.\n\nThree four five.") == \
        ("One two.\n\nThree four five.", 0)
    # a paragraph rewritten as a whole (5 sentences -> 1) can't be aligned: typed as improved,
    # while the other paragraph still gets its pair
    o = "A b c. D e f. G h i. J k l. M n o.\n\nI has a dog."
    i = "Everything said in one new sentence.\n\nI have a dog."
    marked, k, plan = _check(o, i)
    assert k == 1 and marked.startswith("Everything said") and plan.revisions[0].first == "I has a dog."
    # a backslash could turn into a parser escape: that paragraph is left unpaired
    assert P.revision_spans("C:\\dir is bad. Ok.", "C:\\dir is good. Ok.") is None


def test_revision_pairs_type_back_to_improved_text():
    from humantyper.backends import FakeBackend
    from humantyper.engine import Settings, TypingEngine
    o = "Cooking teach students many usefull skills. It also save money.\n\nSharing a meal [with friends] is fun."
    i = "Cooking teaches students many useful skills. It also saves money.\n\nSharing a meal [with friends] is great fun."
    marked, k = P.build_revision_text(o, i)
    assert k == 3
    be = FakeBackend()
    TypingEngine(be, Settings(typo_rate=0.0, seed=4, locate_mode="count"), sleep=lambda s: None).run(marked)
    assert be.text == i


# ---- improve_text (paragraph by paragraph) ---------------------------------------
class FakeEngine:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls, self.started = [], None

    def ensure_started(self, path):
        self.started = path

    def generate(self, messages, max_tokens=600, temperature=0.8):
        self.calls.append((messages, max_tokens, temperature))
        ans = self.answers.pop(0)
        for k in range(0, len(ans), 7):
            yield ans[k:k + 7]


def test_improve_text_per_paragraph_keeps_breaks_and_bad_answers():
    text = "I has a cat and it are nice.\n\nTitle\n\nWe goes to the park every day.\n\nThis one get garbage."
    eng = FakeEngine(["Here is the improved text:\nI have a cat and it is nice.",
                      "We go to the park every day.",
                      "x"])                                          # unusable -> original kept
    chunks, progress, stats = [], [], {}
    out = rewrite.improve_text(eng, "model.gguf", text, "fix", "auto", "qwen3-4b", on_chunk=chunks.append,
                               on_progress=lambda d, n, t: progress.append((d, n, t)), stats=stats)
    assert out == "I have a cat and it is nice.\n\nTitle\n\nWe go to the park every day.\n\nThis one get garbage."
    assert eng.started == "model.gguf" and len(eng.calls) == 3       # 'Title' is not sent to the model
    msgs, max_tok, temp = eng.calls[0]
    assert msgs[1]["content"] == "I has a cat and it are nice. /no_think"
    assert max_tok == P.improve_max_tokens("I has a cat and it are nice.") and temp == P.improve_temperature("fix")
    assert "".join(chunks).startswith("Here is the improved text:")  # raw live preview
    assert progress[0] == (0, 3, "") and progress[1] == (1, 3, "I have a cat and it is nice.\n\nTitle\n\n")
    assert progress[-1] == (3, 3, out)
    assert stats == {"paragraphs": 3, "done": 3, "kept": 1}
    marked, k = P.build_revision_text(text, out)
    assert k == 2 and parse(marked).expected() == out and parse(marked).text == text


def test_improve_text_cancel_keeps_rest_as_written():
    text = "First paragraph has words.\n\nSecond paragraph has words."
    eng = FakeEngine(["First paragraph has more words.", "never used"])
    flag = {"stop": False}

    def on_progress(d, n, t):
        if d == 1:
            flag["stop"] = True
    stats = {}
    out = rewrite.improve_text(eng, None, text, cancelled=lambda: flag["stop"], on_progress=on_progress, stats=stats)
    assert out == "First paragraph has more words.\n\nSecond paragraph has words."
    assert eng.started is None and len(eng.calls) == 1 and stats["done"] == 1


def test_ai_settings_keep_improve_options(tmp_path):
    p = str(tmp_path / "ai.json")
    d = S.load(p)
    assert d["mode"] == "write" and d["improve_style"] == "clearer" and d["improve_revise"] is True
    d.update(mode="improve", improve_style="shorter", improve_revise=False, revisions="5")
    S.save(d, p)
    d2 = S.load(p)
    assert (d2["mode"], d2["improve_style"], d2["improve_revise"], d2["revisions"]) == ("improve", "shorter", False, "5")
