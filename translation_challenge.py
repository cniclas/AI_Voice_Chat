"""The translation challenge: build a graded lesson from a Wikipedia article,
then mark the student's spoken translation of it.

This is the local-model counterpart of the two Claude skills in
`.claude/skills/`. `language-lesson` writes the story and its aligned literal
gloss; `translation-review` marks the student reading that story back in
English. Both are prompt-shaped jobs, so the browser session can do them
against Ollama — the instructions come straight out of the skills' reference
files via `skill_refs`, so the two paths stay calibrated to the same scale
instead of drifting into two different B1s.

What runs here and what runs in Claude differ in one honest way: Claude reads
the whole reference file and reasons over it, while an 8B model gets the
handful of sections that apply to today's level and focus. The work is split
into small, separately-validated calls for the same reason — one prompt asking
llama3.1 for a story, twelve glosses, grammar notes and a vocabulary table at
once reliably drops half of them.

Nothing here talks to the network except through `curriculum.chat_completion`.
"""

import re

import skill_refs
from curriculum import chat_completion_json, top_vocab_to_practice

DEFAULT_LEVEL = skill_refs.DEFAULT_LEVEL

# A B1 story is 150-220 words, so ~15 sentences. The cap only exists to stop a
# runaway generation from turning into 40 sequential gloss calls.
MAX_SENTENCES = 20

# The gloss and review calls carry several kB of skill reference material on
# top of the story itself; the default 8k context leaves no room for the
# review's transcript once the trap catalogue is in the system prompt.
GLOSS_NUM_CTX = 8192
REVIEW_NUM_CTX = 16384

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


# ---------------------------------------------------------------------------
# Story generation
# ---------------------------------------------------------------------------

LESSON_STORY_SYSTEM = (
    "You write graded stories for language learners. You are given the CEFR band for "
    "the target level, the forms that level admits in the target language, and the "
    "grammar focus the story has to carry. Follow them exactly: importing a form from "
    "a higher level is the failure this job is judged on, and the 'out' list matters as "
    "much as the 'in' list.\n\n"
    "{writing_guidance}\n\n"
    "Reply with JSON only."
)

LESSON_STORY_USER = (
    "Write a new semi-fictional story in Spanish based on the source material below. "
    "Invent people and detail; keep the real subject recognizable.\n\n"
    "SOURCE — {article_title}:\n{article_extract}\n\n"
    "TARGET LEVEL {level}:\n{level_band}\n\n"
    "SPANISH FORMS AT {level} (cumulative; respect the 'Out' list):\n{language_forms}\n\n"
    "GRAMMAR FOCUS — {focus}:\n{focus_pattern}\n\n"
    "{vocab_instruction}"
    "The student will translate this story aloud into English sentence by sentence, so "
    "every sentence must be worth decoding: no filler, and no sentence that is just a "
    "restatement of the one before it.\n\n"
    'Return JSON: {{"title": "<short Spanish title>", "story": "<the story in Spanish, plain prose>"}}'
)

STORY_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "story": {"type": "string"}},
    "required": ["title", "story"],
}


# ---------------------------------------------------------------------------
# Aligned translation (the answer key)
# ---------------------------------------------------------------------------

GLOSS_SYSTEM = (
    "You produce the aligned translation of one sentence at a time for a language "
    "lesson. Two lines per sentence, and they do different jobs — the literal line "
    "preserves the original's word order and morphology, the natural line reads like "
    "ordinary English.\n\n"
    "{aligned_guidance}\n\n"
    "{glossing_notes}\n\n"
    "Grammar tags belong on the distinctions this lesson is drilling ({focus}); "
    "leave them off everything else.\n\n"
    "Reply with JSON only."
)

GLOSS_USER = (
    "Story so far (context only — do not translate it):\n{story}\n\n"
    "Gloss sentence {n} of that story:\n{sentence}\n\n"
    'Return JSON: {{"lit": "<word-for-word gloss, original word order>", '
    '"nat": "<idiomatic English>"}}'
)

GLOSS_SCHEMA = {
    "type": "object",
    "properties": {"lit": {"type": "string"}, "nat": {"type": "string"}},
    "required": ["lit", "nat"],
}


# ---------------------------------------------------------------------------
# Focus notes, vocabulary, speaking prompts
# ---------------------------------------------------------------------------

NOTES_SYSTEM = (
    "You write the study notes that follow a graded reading lesson.\n\n"
    "{notes_guidance}\n\n"
    "Reply with JSON only."
)

NOTES_USER = (
    "Story ({level}, focus: {focus}):\n{story}\n\n"
    "Write:\n"
    "- three or four grammar points about {focus}, each quoting a phrase from THIS story, "
    "with the rule in one line and the mistake an English speaker typically makes;\n"
    "- a vocabulary table of words at or above {level} plus any idiom whose literal "
    "translation looks strange, skipping obvious cognates;\n"
    "- three speaking prompts in Spanish that cannot be answered without using the focus "
    "structure.\n\n"
    'Return JSON: {{"notes": [{{"point": "<short label>", "example": "<phrase from the story>", '
    '"rule": "<one line>", "mistake": "<what English speakers get wrong>"}}], '
    '"vocab": [{{"word": "<Spanish>", "literal": "<word-for-word>", "english": "<meaning>", '
    '"note": "<short note or empty>"}}], "speaking_prompts": ["<question in Spanish>"]}}'
)

NOTES_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "string"},
                    "example": {"type": "string"},
                    "rule": {"type": "string"},
                    "mistake": {"type": "string"},
                },
                "required": ["point", "example", "rule", "mistake"],
            },
        },
        "vocab": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "word": {"type": "string"},
                    "literal": {"type": "string"},
                    "english": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["word", "english"],
            },
        },
        "speaking_prompts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["notes", "vocab", "speaking_prompts"],
}


def split_sentences(story: str) -> list[str]:
    """Split the story into the units the student translates one at a time.

    A fragment starting in lower case is not a new sentence — it is the tail of
    the previous one after an ellipsis — so it gets glued back on rather than
    becoming a numbered line the student is asked to translate.
    """
    flat = " ".join(story.split())
    sentences: list[str] = []
    for part in (p.strip() for p in _SENTENCE_SPLIT.split(flat)):
        if not part:
            continue
        first_letter = next((c for c in part if c.isalpha()), "")
        if sentences and first_letter and first_letter.islower():
            sentences[-1] = f"{sentences[-1]} {part}"
        else:
            sentences.append(part)
    return sentences[:MAX_SENTENCES]


def build_lesson(article_title: str, article_extract: str, profile: dict,
                 progress=None) -> dict:
    """Build the full lesson: story, per-sentence aligned translation, focus
    notes, vocabulary, speaking prompts.

    `progress(done, total, label)` is called between steps so a caller can show
    what the pipeline is doing — glossing a dozen sentences is a minute of
    local inference and silence looks like a hang.

    Raises upward if the story itself fails; the notes and any single gloss are
    treated as optional, since a lesson missing one sentence's gloss is still
    usable and a session that dies at sentence nine is not.
    """
    level = profile.get("level") or DEFAULT_LEVEL
    focus = skill_refs.pick_focus(profile)
    practice_words = top_vocab_to_practice(profile)

    def report(done, total, label):
        if progress:
            progress(done, total, label)

    # Steps: the story, one per sentence, then the notes. The sentence count
    # isn't known until the story exists, so the first step reports no total.
    report(0, 0, "writing today's story")
    story = _generate_story(article_title, article_extract, level, focus, practice_words)
    sentences = split_sentences(story["story"])
    total = len(sentences) + 2

    aligned = []
    for i, sentence in enumerate(sentences, start=1):
        report(i, total, f"glossing sentence {i} of {len(sentences)}")
        aligned.append({"n": i, "es": sentence, **_gloss_sentence(story["story"], sentence, i, focus)})

    report(total - 1, total, "writing the grammar notes")
    notes = _generate_notes(story["story"], level, focus)

    report(total, total, "lesson ready")
    return {
        "title": story["title"],
        "story": story["story"],
        "level": level,
        "focus": focus,
        "source": article_title,
        "sentences": aligned,
        "notes": notes.get("notes", []),
        "vocab": notes.get("vocab", []),
        "speaking_prompts": notes.get("speaking_prompts", []),
        "practice_words": practice_words,
    }


def _generate_story(article_title: str, article_extract: str, level: str,
                    focus: str, practice_words: list[str]) -> dict:
    vocab_instruction = (
        f"Weave these words the student needs to practice in naturally: "
        f"{', '.join(practice_words)}.\n\n" if practice_words else ""
    )
    messages = [
        {"role": "system", "content": LESSON_STORY_SYSTEM.format(
            writing_guidance=skill_refs.lesson_guidance("Writing the story"))},
        {"role": "user", "content": LESSON_STORY_USER.format(
            article_title=article_title,
            article_extract=article_extract,
            level=level,
            level_band=skill_refs.level_band(level),
            language_forms=skill_refs.language_forms(level),
            focus=focus,
            focus_pattern=skill_refs.focus_pattern(focus),
            vocab_instruction=vocab_instruction,
        )},
    ]
    story = chat_completion_json(messages, STORY_SCHEMA)
    if not story.get("story", "").strip():
        raise ValueError("the model returned an empty story")
    return story


def _gloss_sentence(story: str, sentence: str, n: int, focus: str) -> dict:
    """One sentence's literal and natural lines. A failed gloss falls back to
    an empty pair rather than sinking the lesson — the sentence still gets read
    and translated, it just has no answer key."""
    messages = [
        {"role": "system", "content": GLOSS_SYSTEM.format(
            aligned_guidance=skill_refs.lesson_guidance("The aligned translation"),
            glossing_notes=skill_refs.glossing_notes(),
            focus=focus)},
        {"role": "user", "content": GLOSS_USER.format(story=story, n=n, sentence=sentence)},
    ]
    try:
        gloss = chat_completion_json(messages, GLOSS_SCHEMA, num_ctx=GLOSS_NUM_CTX)
    except (ValueError, KeyError):
        return {"lit": "", "nat": ""}
    return {"lit": gloss.get("lit", "").strip(), "nat": gloss.get("nat", "").strip()}


def _generate_notes(story: str, level: str, focus: str) -> dict:
    messages = [
        {"role": "system", "content": NOTES_SYSTEM.format(
            notes_guidance=skill_refs.lesson_guidance("Focus notes and vocabulary"))},
        {"role": "user", "content": NOTES_USER.format(story=story, level=level, focus=focus)},
    ]
    try:
        return chat_completion_json(messages, NOTES_SCHEMA)
    except (ValueError, KeyError):
        return {}


# ---------------------------------------------------------------------------
# Marking the spoken translation
# ---------------------------------------------------------------------------

REVIEW_SYSTEM = (
    "You mark a student's spoken translation of a Spanish story into English, their "
    "native language. Their English is not the subject — it is the readout. Read every "
    "deviation as a question about the Spanish, and if a deviation has no plausible "
    "Spanish explanation it is the microphone, not the student.\n\n"
    "The transcript comes from Whisper, so it has no spelling of the student's making. "
    "Apply these filters before anything becomes a finding:\n\n"
    "{judging_rules}\n\n"
    "Traps specific to reading Spanish into English:\n\n"
    "{traps}\n\n"
    "Reply with JSON only."
)

REVIEW_USER = (
    "LESSON: {title} · level {level} · focus: {focus}\n\n"
    "The source, sentence by sentence, with the answer key. 'Lit' is the word-for-word "
    "gloss, 'Nat' is what a competent translation looks like:\n\n{aligned}\n\n"
    "WHAT THE STUDENT SAID (Whisper transcript of them translating aloud):\n{transcript}\n\n"
    "Report only deviations that survive both filters. For each one give the sentence "
    "number, the verdict, what they said, the Spanish, its literal gloss, and an "
    "explanation that points at the specific word or ending they lost.\n"
    "- verdict \"missed\": the meaning is wrong. 'correction' is the right rendering.\n"
    "- verdict \"blurred\": the meaning survives but the distinction this lesson drills "
    "was flattened. 'correction' is the rendering that keeps it.\n"
    "- verdict \"check\": English cannot show whether they understood. 'correction' is "
    "the single question that would settle it.\n"
    "A good translation produces few findings or none, and that is a real result — do "
    "not invent borderline ones. Also list two or three things they got right that were "
    "not easy.\n\n"
    'Return JSON: {{"summary": "<two sentences: what they clearly understood, and the '
    'through-line of what they did not>", "findings": [{{"sentence": <number>, '
    '"verdict": "missed|blurred|check", "type": "grammar|vocabulary|expression", '
    '"topic": "<short label, e.g. \'indirect object pronouns\'>", "said": "<their words>", '
    '"spanish": "<the Spanish sentence>", "literal": "<its literal gloss>", '
    '"explanation": "<one or two sentences>", "correction": "<corrected rendering, or the '
    'question for a check>"}}], "holding_up": ["<something specific they got right>"], '
    '"vocab_to_practice": ["<Spanish words they misread or lacked>"]}}'
)

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["missed", "blurred", "check"]},
                    "type": {"type": "string", "enum": ["grammar", "vocabulary", "expression"]},
                    "topic": {"type": "string"},
                    "said": {"type": "string"},
                    "spanish": {"type": "string"},
                    "literal": {"type": "string"},
                    "explanation": {"type": "string"},
                    "correction": {"type": "string"},
                },
                "required": ["sentence", "verdict", "type", "topic", "said",
                             "spanish", "explanation", "correction"],
            },
        },
        "holding_up": {"type": "array", "items": {"type": "string"}},
        "vocab_to_practice": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "findings", "holding_up", "vocab_to_practice"],
}

VERDICTS = ("missed", "blurred", "check")


def _format_aligned(lesson: dict) -> str:
    lines = []
    for s in lesson["sentences"]:
        lines.append(f"{s['n']}. {s['es']}")
        if s.get("lit"):
            lines.append(f"   Lit: {s['lit']}")
        if s.get("nat"):
            lines.append(f"   Nat: {s['nat']}")
    return "\n".join(lines)


def review_translation(lesson: dict, transcript: str) -> dict:
    """Mark one spoken translation against the lesson's answer key.

    Returns the review dict; raises upward (ValueError / requests exceptions)
    if Ollama can't produce one, so the caller can keep the session going
    without a review rather than pretending it marked something.
    """
    messages = [
        {"role": "system", "content": REVIEW_SYSTEM.format(
            judging_rules=skill_refs.judging_rules(),
            traps=skill_refs.comprehension_traps())},
        {"role": "user", "content": REVIEW_USER.format(
            title=lesson["title"],
            level=lesson.get("level", DEFAULT_LEVEL),
            focus=lesson.get("focus", ""),
            aligned=_format_aligned(lesson),
            transcript=transcript,
        )},
    ]
    review = chat_completion_json(messages, REVIEW_SCHEMA, num_ctx=REVIEW_NUM_CTX)
    review["findings"] = [
        f for f in review.get("findings", []) if f.get("verdict") in VERDICTS
    ]
    return review


def findings_by_verdict(review: dict, *verdicts: str) -> list[dict]:
    return [f for f in review.get("findings", []) if f.get("verdict") in verdicts]


def review_to_analysis(review: dict) -> dict:
    """Reshape a review into what `curriculum.merge_analysis_into_profile()`
    consumes, so a weakness found by reading lands in the same `occurrences`
    tally as one found by speaking — the same contract
    `.claude/skills/translation-review/scripts/record_review.py` uses.

    "Check" findings are dropped: they are open questions, not established
    weaknesses, and recording them would count an ambiguity as a mistake.
    """
    weaknesses = []
    for f in findings_by_verdict(review, "missed", "blurred"):
        weaknesses.append({
            "type": f.get("type", "grammar"),
            "topic": f.get("topic", "").strip() or "reading comprehension",
            "evidence": f.get("said", ""),
            "correction": f.get("correction", ""),
            "explanation": f.get("explanation", ""),
        })
    return {
        "summary": review.get("summary", ""),
        "weaknesses": weaknesses[:5],
        "vocab_to_practice": review.get("vocab_to_practice", [])[:10],
    }


def merge_review_into_analysis(analysis: dict | None, review: dict | None) -> dict | None:
    """Fold the translation findings into the conversation analysis so the
    session ends with one picture of the student. Merging before the profile
    write (rather than merging twice) keeps `occurrences` counting sessions,
    not passes over the same session."""
    if not review:
        return analysis
    from_review = review_to_analysis(review)
    if analysis is None:
        return from_review

    merged = dict(analysis)
    merged["weaknesses"] = (analysis.get("weaknesses", []) + from_review["weaknesses"])[:8]
    seen = {v.casefold() for v in analysis.get("vocab_to_practice", [])}
    merged["vocab_to_practice"] = analysis.get("vocab_to_practice", []) + [
        v for v in from_review["vocab_to_practice"] if v.casefold() not in seen
    ]
    merged["summary"] = " ".join(
        part for part in (analysis.get("summary", ""), from_review["summary"]) if part
    )
    return merged


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_lesson_markdown(lesson: dict) -> str:
    """The lesson in the shape the language-lesson skill specifies — the story
    once as unbroken prose (that is what gets read aloud), then again split
    across the aligned reading."""
    out = [
        f"# {lesson['title']}",
        "",
        f"**Nivel:** {lesson.get('level', DEFAULT_LEVEL)} · "
        f"**Enfoque:** {lesson.get('focus', '')}",
        f"**Source:** Wikipedia — {lesson.get('source', '')}",
        "",
        "## Historia",
        "",
        lesson["story"],
        "",
        "## Lectura alineada",
        "",
    ]
    for s in lesson["sentences"]:
        out.append(f"**{s['n']}.** {s['es']}")
        if s.get("lit"):
            out.append(f"- *Lit.* {s['lit']}")
        if s.get("nat"):
            out.append(f"- *Nat.* {s['nat']}")
        out.append("")

    if lesson.get("notes"):
        out += [f"## Notas de gramática — {lesson.get('focus', '')}", ""]
        for note in lesson["notes"]:
            out.append(f"**{note.get('point', '')}** — *{note.get('example', '')}*")
            out.append(note.get("rule", ""))
            if note.get("mistake"):
                out.append(f"Typical English-speaker mistake: {note['mistake']}")
            out.append("")

    if lesson.get("vocab"):
        out += ["## Vocabulario", "", "| Español | Literal | English | Nota |",
                "|---|---|---|---|"]
        for v in lesson["vocab"]:
            out.append(
                f"| {v.get('word', '')} | {v.get('literal', '')} | "
                f"{v.get('english', '')} | {v.get('note', '')} |"
            )
        out.append("")

    if lesson.get("speaking_prompts"):
        out += ["## Para hablar", ""]
        for i, prompt in enumerate(lesson["speaking_prompts"], start=1):
            out.append(f"{i}. {prompt}")
        out.append("")

    return "\n".join(out)


_VERDICT_HEADING = {
    "missed": "missed",
    "blurred": "blurred",
    "check": "worth checking",
}


def render_review_markdown(review: dict, lesson: dict) -> str:
    """The review in the translation-review skill's layout: what to fix, what
    to check, what held up."""
    out = [f"# Translation review — {lesson['title']}", "", review.get("summary", ""), ""]

    to_fix = findings_by_verdict(review, "missed", "blurred")
    if to_fix:
        out += ["## What to fix", ""]
        for f in to_fix:
            out.append(f"**Sentence {f.get('sentence', '?')} — {_VERDICT_HEADING[f['verdict']]}**")
            out.append(f"> You said: \"{f.get('said', '')}\"")
            out.append(f"> Spanish: *{f.get('spanish', '')}*")
            if f.get("literal"):
                out.append(f"> Literal: *{f['literal']}*")
            out.append("")
            out.append(f.get("explanation", ""))
            if f.get("correction"):
                out.append(f"**Better:** {f['correction']}")
            out.append("")

    checks = findings_by_verdict(review, "check")
    if checks:
        out += ["## Worth checking", ""]
        for f in checks:
            out.append(f"**Sentence {f.get('sentence', '?')}** — {f.get('correction') or f.get('explanation', '')}")
            out.append("")

    if review.get("holding_up"):
        out += ["## Holding up well", ""]
        out += [f"- {item}" for item in review["holding_up"]]
        out.append("")

    return "\n".join(out)


def review_chat_text(review: dict) -> str:
    """A plain-text rendering for the chat bubble. The bubble shows text, not
    markdown, so the file's `**bold**` and blockquotes would only be noise."""
    lines = [review.get("summary", "").strip()]

    for f in findings_by_verdict(review, "missed", "blurred"):
        lines.append("")
        lines.append(f"Sentence {f.get('sentence', '?')} ({_VERDICT_HEADING[f['verdict']]}): "
                     f"{f.get('spanish', '')}")
        lines.append(f"You said: {f.get('said', '')}")
        if f.get("explanation"):
            lines.append(f.get("explanation"))
        if f.get("correction"):
            lines.append(f"Better: {f['correction']}")

    checks = findings_by_verdict(review, "check")
    if checks:
        lines.append("")
        lines.append("Worth checking:")
        for f in checks:
            lines.append(f"- Sentence {f.get('sentence', '?')}: "
                         f"{f.get('correction') or f.get('explanation', '')}")

    if review.get("holding_up"):
        lines.append("")
        lines.append("Holding up well:")
        lines += [f"- {item}" for item in review["holding_up"]]

    return "\n".join(line for line in lines if line is not None).strip()


def spoken_review_summary(review: dict) -> str:
    """What the tutor says out loud after marking. Kokoro reads this, so it
    stays short and skips the sentence-by-sentence detail that is easier to
    read than to hear."""
    parts = [review.get("summary", "").strip()]
    to_fix = findings_by_verdict(review, "missed", "blurred")
    checks = findings_by_verdict(review, "check")

    if to_fix:
        first = to_fix[0]
        parts.append(f"The main thing to look at is sentence {first.get('sentence', '?')}. "
                     f"{first.get('explanation', '')}")
        if len(to_fix) > 1:
            parts.append(f"There are {len(to_fix) - 1} more notes written up for you.")
    elif not checks:
        parts.append("Nothing to correct — that was a solid reading.")

    if checks:
        parts.append(f"One question: {checks[0].get('correction') or checks[0].get('explanation', '')}")

    parts.append("Ask me anything about the story, or we can just keep talking.")
    return " ".join(p for p in parts if p)
