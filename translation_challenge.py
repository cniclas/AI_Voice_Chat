"""The translation challenge: build a graded lesson from a Wikipedia article,
then mark the student's spoken translation of it.

The challenge runs in either direction, and the direction decides what is being
tested:

- ``into_native`` — the student hears the story in the language they are
  learning and says what it means in their own. This tests *comprehension*:
  their native language is only the readout, so every deviation is read as a
  question about the target language.
- ``into_target`` — the student hears the same story's natural translation in
  their own language and says it back in the language they are learning. This
  tests *production*: everything they utter is under test — gender, agreement,
  tense and mood, prepositions, word order, word choice.

Both directions share one lesson. The story is always written in the target
language (that is where level and grammar focus mean anything) and glossed
sentence by sentence into the native one; the reverse direction simply hands
the student the natural (`nat`) lines as its source text and marks their speech
against the target sentences those lines came from. One build, two exercises,
and the answer key is aligned by construction.

This is the local-model counterpart of the two Claude skills in
`.claude/skills/`. `language-lesson` writes the story and its aligned literal
gloss; `translation-review` marks the student reading that story back in their
native language. Both are prompt-shaped jobs, so the browser session can do them
against Ollama — the instructions come straight out of the skills' reference
files via `skill_refs`, so the two paths stay calibrated to the same scale
instead of drifting into two different B1s. The production direction has no
skill of its own; it reuses the same judging filters and the same pair-specific
trap catalogue, read from the other side.

What runs here and what runs in Claude differ in one honest way: Claude reads
the whole reference file and reasons over it, while an 8B model gets the
handful of sections that apply to today's level and focus. The work is split
into small, separately-validated calls for the same reason — one prompt asking
llama3.1 for a story, twelve glosses, grammar notes and a vocabulary table at
once reliably drops half of them.

Nothing here talks to the network except through `curriculum.chat_completion`.
"""

import re

import languages
import skill_refs
from curriculum import chat_completion_json, top_vocab_to_practice

DEFAULT_LEVEL = skill_refs.DEFAULT_LEVEL

# Which way the student translates. `into_native` is the reading challenge (the
# original one); `into_target` is the reverse, where they produce the language
# they are learning.
INTO_NATIVE = "into_native"
INTO_TARGET = "into_target"
DIRECTIONS = (INTO_NATIVE, INTO_TARGET)

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
    "Write a new semi-fictional story in {target_name} based on the source material "
    "below. Invent people and detail; keep the real subject recognizable.\n\n"
    "SOURCE — {article_title}:\n{article_extract}\n\n"
    "TARGET LEVEL {level}:\n{level_band}\n\n"
    "{target_upper} FORMS AT {level} (cumulative; respect the 'Out' list):\n{language_forms}\n\n"
    "{focus_block}"
    "{vocab_instruction}"
    "The student will translate this story aloud into {native_name} sentence by "
    "sentence, so every sentence must be worth decoding: no filler, and no sentence "
    "that is just a restatement of the one before it.\n\n"
    'Return JSON: {{"title": "<short title in {target_name}>", '
    '"story": "<the story in {target_name}, plain prose>"}}'
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
    "ordinary {native_name}. Both lines are written in {native_name}; the story is in "
    "{target_name}.\n\n"
    "{aligned_guidance}\n\n"
    "{glossing_notes}\n\n"
    "{focus_tags}"
    "Reply with JSON only."
)

GLOSS_USER = (
    "Story so far (context only — do not translate it):\n{story}\n\n"
    "Gloss sentence {n} of that story:\n{sentence}\n\n"
    'Return JSON: {{"lit": "<word-for-word gloss in {native_name}, original word order>", '
    '"nat": "<idiomatic {native_name}>"}}'
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
    "The student's native language is {native_name}; write the explanations in "
    "{native_name} and keep every example in {target_name}.\n\n"
    "Write:\n"
    "- three or four grammar points about {focus}, each quoting a phrase from THIS story, "
    "with the rule in one line and the mistake a {native_name} speaker typically makes;\n"
    "- a vocabulary table of words at or above {level} plus any idiom whose literal "
    "translation looks strange, skipping obvious cognates;\n"
    "- three speaking prompts in {target_name} that cannot be answered without using the "
    "focus structure.\n\n"
    'Return JSON: {{"notes": [{{"point": "<short label>", "example": "<phrase from the story>", '
    '"rule": "<one line>", "mistake": "<what {native_name} speakers get wrong>"}}], '
    '"vocab": [{{"word": "<{target_name}>", "literal": "<word-for-word>", '
    '"native": "<meaning in {native_name}>", '
    '"note": "<short note or empty>"}}], "speaking_prompts": ["<question in {target_name}>"]}}'
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
                    "native": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["word", "native"],
            },
        },
        "speaking_prompts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["notes", "vocab", "speaking_prompts"],
}


# ---------------------------------------------------------------------------
# The reverse direction's source text
# ---------------------------------------------------------------------------

TITLE_SYSTEM = (
    "You translate the title of a short story into {native_name}. Keep it a title: "
    "short, no explanation, no quotation marks.\n\nReply with JSON only."
)

TITLE_USER = (
    "The story ({target_name}, context only):\n{story}\n\n"
    "Its title: {title}\n\n"
    'Return JSON: {{"title": "<the title in {native_name}>"}}'
)

TITLE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
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
                 target: str, native: str, direction: str = INTO_NATIVE,
                 progress=None) -> dict:
    """Build the full lesson: story, per-sentence aligned translation, focus
    notes, vocabulary, speaking prompts.

    The story is written in `target` and every translation line in `native` —
    the student's own language, which is what makes the literal line a readable
    answer key rather than a second thing to decode. That holds in both
    directions: level and grammar focus only mean something applied to the
    language being learned, so the reverse challenge writes the same target
    story and then hands the student its natural translation to work back from.

    `direction` decides which side of the aligned pair the student is given
    (`lesson["prompt"]`) and which language they answer in
    (`lesson["speak_lang"]`).

    `progress(done, total, label)` is called between steps so a caller can show
    what the pipeline is doing — glossing a dozen sentences is a minute of
    local inference and silence looks like a hang.

    Raises upward if the story itself fails; the notes and any single gloss are
    treated as optional, since a lesson missing one sentence's gloss is still
    usable and a session that dies at sentence nine is not. The reverse
    direction leans on those glosses harder — they *are* the text the student
    translates — so a story that produced none of them raises rather than
    handing over an empty prompt.
    """
    level = profile.get("level") or DEFAULT_LEVEL
    focus = skill_refs.pick_focus(profile, target)
    practice_words = top_vocab_to_practice(profile)
    reverse = direction == INTO_TARGET

    def report(done, total, label):
        if progress:
            progress(done, total, label)

    # Steps: the story, one per sentence, then the notes (plus the title
    # translation in the reverse direction). The sentence count isn't known
    # until the story exists, so the first step reports no total.
    report(0, 0, "writing today's story")
    story = _generate_story(article_title, article_extract, level, focus,
                            practice_words, target, native)
    sentences = split_sentences(story["story"])
    total = len(sentences) + (3 if reverse else 2)

    aligned = []
    for i, sentence in enumerate(sentences, start=1):
        report(i, total, f"glossing sentence {i} of {len(sentences)}")
        aligned.append({"n": i, "text": sentence,
                        **_gloss_sentence(story["story"], sentence, i, focus, target, native)})

    lesson = {
        "title": story["title"],
        "story": story["story"],
        "level": level,
        "focus": focus,
        "target": target,
        "native": native,
        "direction": direction,
        "source": article_title,
        "sentences": aligned,
        "practice_words": practice_words,
    }

    if reverse:
        report(len(sentences) + 1, total,
               f"putting today's text into {languages.name(native)}")
        lesson["prompt"] = _native_prompt(story, aligned, target, native)
        lesson["speak_lang"] = target
    else:
        lesson["prompt"] = {"lang": target, "title": story["title"],
                            "text": story["story"]}
        lesson["speak_lang"] = native

    report(total - 1, total, "writing the grammar notes")
    notes = _generate_notes(story["story"], level, focus, target, native)
    lesson["notes"] = notes.get("notes", [])
    lesson["vocab"] = notes.get("vocab", [])
    lesson["speaking_prompts"] = notes.get("speaking_prompts", [])

    report(total, total, "lesson ready")
    return lesson


def _native_prompt(story: dict, aligned: list[dict], target: str, native: str) -> dict:
    """The reverse direction's source text: the story as the student hears it,
    in their own language.

    It is assembled from the sentences' natural translations rather than
    generated separately, so what they are asked to say back lines up with the
    answer key sentence for sentence. A sentence whose natural line failed
    falls back to its literal one — worse prose, but still the same sentence,
    where a missing line would silently shorten the story.
    """
    parts = [(s.get("nat") or s.get("lit") or "").strip() for s in aligned]
    text = " ".join(p for p in parts if p)
    if not text:
        raise ValueError(
            f"no {languages.name(native)} translation was produced for the story")
    return {"lang": native, "title": _translate_title(story, target, native), "text": text}


def _translate_title(story: dict, target: str, native: str) -> str:
    """The story's title in the student's language. Falls back to the target
    title, which is a poor prompt but never a missing one."""
    messages = [
        {"role": "system", "content": TITLE_SYSTEM.format(**_names(target, native))},
        {"role": "user", "content": TITLE_USER.format(
            story=story["story"], title=story["title"], **_names(target, native))},
    ]
    try:
        return chat_completion_json(messages, TITLE_SCHEMA)["title"].strip() or story["title"]
    except (ValueError, KeyError, AttributeError):
        return story["title"]


def direction_of(lesson: dict) -> str:
    return lesson.get("direction", INTO_NATIVE)


def prompt_side(lesson: dict) -> dict:
    """What the student is given to work from: ``{"lang", "title", "text"}``.

    Defaulted for a lesson built before the reverse direction existed, so a
    caller never has to know which shape it has.
    """
    return lesson.get("prompt") or {
        "lang": lesson["target"], "title": lesson["title"], "text": lesson["story"]}


def speak_lang(lesson: dict) -> str:
    """The language the spoken translation has to be in."""
    return lesson.get("speak_lang") or lesson["native"]


def _names(target: str, native: str) -> dict:
    return {
        "target_name": languages.name(target),
        "native_name": languages.name(native),
    }


def _generate_story(article_title: str, article_extract: str, level: str,
                    focus: str, practice_words: list[str],
                    target: str, native: str) -> dict:
    vocab_instruction = (
        f"Weave these words the student needs to practice in naturally: "
        f"{', '.join(practice_words)}.\n\n" if practice_words else ""
    )
    # A language file with no focus patterns yet leaves `focus` empty; drop the
    # block entirely rather than asking the model to carry a blank focus.
    focus_block = (
        f"GRAMMAR FOCUS — {focus}:\n{skill_refs.focus_pattern(focus, target)}\n\n"
        if focus else ""
    )
    messages = [
        {"role": "system", "content": LESSON_STORY_SYSTEM.format(
            writing_guidance=skill_refs.lesson_guidance("Writing the story"))},
        {"role": "user", "content": LESSON_STORY_USER.format(
            article_title=article_title,
            article_extract=article_extract,
            level=level,
            level_band=skill_refs.level_band(level),
            language_forms=skill_refs.language_forms(level, target),
            target_upper=languages.name(target).upper(),
            focus_block=focus_block,
            vocab_instruction=vocab_instruction,
            **_names(target, native),
        )},
    ]
    story = chat_completion_json(messages, STORY_SCHEMA)
    if not story.get("story", "").strip():
        raise ValueError("the model returned an empty story")
    return story


def _gloss_sentence(story: str, sentence: str, n: int, focus: str,
                    target: str, native: str) -> dict:
    """One sentence's literal and natural lines. A failed gloss falls back to
    an empty pair rather than sinking the lesson — the sentence still gets read
    and translated, it just has no answer key."""
    focus_tags = (
        f"Grammar tags belong on the distinctions this lesson is drilling ({focus}); "
        "leave them off everything else.\n\n" if focus else ""
    )
    messages = [
        {"role": "system", "content": GLOSS_SYSTEM.format(
            aligned_guidance=skill_refs.lesson_guidance("The aligned translation"),
            glossing_notes=skill_refs.glossing_notes(target, native),
            focus_tags=focus_tags,
            **_names(target, native))},
        {"role": "user", "content": GLOSS_USER.format(
            story=story, n=n, sentence=sentence, **_names(target, native))},
    ]
    try:
        gloss = chat_completion_json(messages, GLOSS_SCHEMA, num_ctx=GLOSS_NUM_CTX)
    except (ValueError, KeyError):
        return {"lit": "", "nat": ""}
    return {"lit": gloss.get("lit", "").strip(), "nat": gloss.get("nat", "").strip()}


def _generate_notes(story: str, level: str, focus: str,
                    target: str, native: str) -> dict:
    messages = [
        {"role": "system", "content": NOTES_SYSTEM.format(
            notes_guidance=skill_refs.lesson_guidance("Focus notes and vocabulary"))},
        {"role": "user", "content": NOTES_USER.format(
            story=story, level=level, focus=focus, **_names(target, native))},
    ]
    try:
        return chat_completion_json(messages, NOTES_SCHEMA)
    except (ValueError, KeyError):
        return {}


# ---------------------------------------------------------------------------
# Marking the spoken translation
# ---------------------------------------------------------------------------

REVIEW_SYSTEM = (
    "You mark a student's spoken translation of a {target_name} story into "
    "{native_name}, their native language. Their {native_name} is not the subject — it "
    "is the readout. Read every deviation as a question about the {target_name}, and if "
    "a deviation has no plausible {target_name} explanation it is the microphone, not "
    "the student.\n\n"
    "The transcript comes from Whisper, so it has no spelling of the student's making. "
    "Apply these filters before anything becomes a finding:\n\n"
    "{judging_rules}\n\n"
    "Traps specific to reading {target_name} into {native_name}:\n\n"
    "{traps}\n\n"
    "Reply with JSON only."
)

REVIEW_USER = (
    "LESSON: {title} · level {level} · focus: {focus}\n\n"
    "The source, sentence by sentence, with the answer key. 'Lit' is the word-for-word "
    "gloss, 'Nat' is what a competent translation looks like:\n\n{aligned}\n\n"
    "WHAT THE STUDENT SAID (Whisper transcript of them translating aloud):\n{transcript}\n\n"
    "Write the summary, every explanation and every correction in {native_name} — the "
    "student reads and hears this review in their own language. Keep 'topic' in English.\n\n"
    "Report only deviations that survive both filters. For each one give the sentence "
    "number, the verdict, what they said, the {target_name} source, its literal gloss, "
    "and an explanation that points at the specific word or ending they lost.\n"
    "- verdict \"missed\": the meaning is wrong. 'correction' is the right rendering.\n"
    "- verdict \"blurred\": the meaning survives but the distinction this lesson drills "
    "was flattened. 'correction' is the rendering that keeps it.\n"
    "- verdict \"check\": {native_name} cannot show whether they understood. "
    "'correction' is the single question that would settle it.\n"
    "A good translation produces few findings or none, and that is a real result — do "
    "not invent borderline ones. Also list two or three things they got right that were "
    "not easy.\n\n"
    'Return JSON: {{"summary": "<two sentences: what they clearly understood, and the '
    'through-line of what they did not>", "findings": [{{"sentence": <number>, '
    '"verdict": "missed|blurred|check", "type": "grammar|vocabulary|expression", '
    '"topic": "<short label in English, e.g. \'indirect object pronouns\'>", '
    '"said": "<their words>", '
    '"source": "<the {target_name} sentence>", "literal": "<its literal gloss>", '
    '"explanation": "<one or two sentences>", "correction": "<corrected rendering, or the '
    'question for a check>"}}], "holding_up": ["<something specific they got right>"], '
    '"vocab_to_practice": ["<{target_name} words they misread or lacked>"]}}'
)

PRODUCTION_REVIEW_SYSTEM = (
    "You mark a student's spoken translation INTO {target_name}, the language they are "
    "learning. They heard a story in {native_name}, their own language, and said it back "
    "in {target_name}. This is the producing direction, so what they said is itself the "
    "subject: gender and agreement, tense and mood, prepositions, word order, and word "
    "choice are all under test. Their {native_name} is not, and neither is anything that "
    "exists only in writing.\n\n"
    "The transcript comes from Whisper, so it is Whisper's spelling of their speech, and "
    "it is a learner's {target_name} being written down by a model that expects fluent "
    "{target_name}. Two consequences: written accents, punctuation and homophones can "
    "never be findings, and an error may have been tidied away before it reached you. "
    "Mark what is in the transcript; never reconstruct what you suspect they must have "
    "said.\n\n"
    "{judging_rules}\n\n"
    "The distinctions this pair blurs — catalogued from the reading direction, but a "
    "{native_name} speaker producing {target_name} drops exactly the same ones, and "
    "reaches for the {native_name} structure when they do:\n\n{traps}\n\n"
    "{focus_block}"
    "Reply with JSON only."
)

PRODUCTION_REVIEW_USER = (
    "LESSON: {title} · level {level} · focus: {focus}\n\n"
    "The {native_name} text the student heard, sentence by sentence, each with the "
    "{target_name} it was translated from — that sentence is the model answer, and its "
    "literal gloss shows the morphology they had to produce:\n\n{aligned}\n\n"
    "WHAT THE STUDENT SAID (Whisper transcript of them speaking {target_name}):\n"
    "{transcript}\n\n"
    "The model answer is one correct rendering, not the only one. Different word order, a "
    "synonym, a simpler construction that says the same thing at this level — all "
    "correct. Flag only what is wrong in {target_name} or what says something else.\n\n"
    "Write the summary, every explanation and every correction in {native_name} — the "
    "student reads and hears this review in their own language, and quote the {target_name} "
    "inside it. Keep 'topic' in English.\n\n"
    "For each finding give the sentence number, the verdict, what they said, the "
    "{target_name} model answer, its literal gloss, and an explanation that points at the "
    "specific word or ending.\n"
    "- verdict \"missed\": what they said does not mean what the source said, or the "
    "{target_name} breaks down badly enough that the meaning goes with it. 'correction' is "
    "a correct rendering.\n"
    "- verdict \"blurred\": understandable, but a required marking is missing or the "
    "distinction this lesson drills was flattened — a calque of the {native_name} wording, "
    "a wrong gender or agreement, a tense that neutralises the contrast. 'correction' is "
    "the rendering that keeps it.\n"
    "- verdict \"check\": the transcript cannot settle it, because the difference lives in "
    "a written accent or a sound Whisper normalises. 'correction' is the single question "
    "that would settle it.\n"
    "A good translation produces few findings or none, and that is a real result — do not "
    "invent borderline ones. Also list two or three things they got right that were not "
    "easy.\n\n"
    'Return JSON: {{"summary": "<two sentences: what they clearly produced well, and the '
    'through-line of what went wrong>", "findings": [{{"sentence": <number>, '
    '"verdict": "missed|blurred|check", "type": "grammar|vocabulary|expression", '
    '"topic": "<short label in English, e.g. \'preterite endings\'>", '
    '"said": "<their {target_name} words>", '
    '"source": "<the {target_name} model answer>", "literal": "<its literal gloss>", '
    '"explanation": "<one or two sentences>", "correction": "<corrected {target_name}, or '
    'the question for a check>"}}], "holding_up": ["<something specific they got right>"], '
    '"vocab_to_practice": ["<{target_name} words they lacked or got wrong>"]}}'
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
                    "source": {"type": "string"},
                    "literal": {"type": "string"},
                    "explanation": {"type": "string"},
                    "correction": {"type": "string"},
                },
                "required": ["sentence", "verdict", "type", "topic", "said",
                             "source", "explanation", "correction"],
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
        lines.append(f"{s['n']}. {s['text']}")
        if s.get("lit"):
            lines.append(f"   Lit: {s['lit']}")
        if s.get("nat"):
            lines.append(f"   Nat: {s['nat']}")
    return "\n".join(lines)


def _format_aligned_production(lesson: dict, names: dict) -> str:
    """The same pairs read the other way round: what the student heard first,
    then the sentence they were supposed to produce."""
    lines = []
    for s in lesson["sentences"]:
        lines.append(f"{s['n']}. {s.get('nat') or s.get('lit', '')}")
        lines.append(f"   {names['target_name']}: {s['text']}")
        if s.get("lit"):
            lines.append(f"   Lit: {s['lit']}")
    return "\n".join(lines)


def _comprehension_review_messages(lesson: dict, transcript: str,
                                   target: str, native: str, names: dict) -> list[dict]:
    return [
        {"role": "system", "content": REVIEW_SYSTEM.format(
            judging_rules=skill_refs.judging_rules(),
            traps=skill_refs.comprehension_traps(target, native),
            **names)},
        {"role": "user", "content": REVIEW_USER.format(
            title=lesson["title"],
            level=lesson.get("level", DEFAULT_LEVEL),
            focus=lesson.get("focus", ""),
            aligned=_format_aligned(lesson),
            transcript=transcript,
            **names,
        )},
    ]


def _production_review_messages(lesson: dict, transcript: str,
                                target: str, native: str, names: dict) -> list[dict]:
    focus = lesson.get("focus", "")
    # The focus pattern names the mistake a learner reliably makes with this
    # structure, which is the single most useful thing to hand a marker of
    # produced language.
    pattern = skill_refs.focus_pattern(focus, target) if focus else ""
    focus_block = (
        f"The structure this lesson drills is {focus}; weight it above everything "
        f"else:\n\n{pattern}\n\n" if pattern else ""
    )
    return [
        {"role": "system", "content": PRODUCTION_REVIEW_SYSTEM.format(
            judging_rules=skill_refs.judging_rules(),
            traps=skill_refs.comprehension_traps(target, native),
            focus_block=focus_block,
            **names)},
        {"role": "user", "content": PRODUCTION_REVIEW_USER.format(
            title=lesson["title"],
            level=lesson.get("level", DEFAULT_LEVEL),
            focus=focus,
            aligned=_format_aligned_production(lesson, names),
            transcript=transcript,
            **names,
        )},
    ]


def review_translation(lesson: dict, transcript: str,
                       target: str | None = None, native: str | None = None) -> dict:
    """Mark one spoken translation against the lesson's answer key.

    The lesson's direction picks the marking job: reading the target language
    into the native one is a comprehension check, where the student's own
    language is only a readout; saying the story back in the target language is
    a production check, where every word they uttered is under test. The two
    share the judging filters, the schema and everything downstream.

    The pair defaults to the one the lesson was built with, since a review is
    only ever of that lesson.

    Returns the review dict; raises upward (ValueError / requests exceptions)
    if Ollama can't produce one, so the caller can keep the session going
    without a review rather than pretending it marked something.
    """
    target = target or lesson["target"]
    native = native or lesson["native"]
    names = _names(target, native)
    build = (_production_review_messages if direction_of(lesson) == INTO_TARGET
             else _comprehension_review_messages)
    review = chat_completion_json(
        build(lesson, transcript, target, native, names),
        REVIEW_SCHEMA, num_ctx=REVIEW_NUM_CTX)
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
    across the aligned reading.

    Section headings are in the target language, which is what the skill asks
    for: the lesson is an artifact of that language, not a document about it.

    A reverse challenge gets one extra section up front: the native-language
    prose the student actually heard. Everything below it — the target story,
    the aligned reading — is then the answer key to it, which is the same
    document read in the other direction.
    """
    target = languages.get(lesson["target"])
    native = languages.get(lesson["native"])
    h = target.lesson_headings
    out = [
        f"# {lesson['title']}",
        "",
        f"**{h.level}:** {lesson.get('level', DEFAULT_LEVEL)} · "
        f"**{h.focus}:** {lesson.get('focus', '')} · "
        f"**{h.to_translate}:** {_direction_label(lesson)}",
        f"**Source:** Wikipedia — {lesson.get('source', '')}",
        "",
    ]

    if direction_of(lesson) == INTO_TARGET:
        prompt = prompt_side(lesson)
        out += [
            f"## {h.to_translate} — {native.endonym}",
            "",
            f"**{prompt['title']}**",
            "",
            prompt["text"],
            "",
        ]

    out += [
        f"## {h.story}",
        "",
        lesson["story"],
        "",
        f"## {h.aligned}",
        "",
    ]
    for s in lesson["sentences"]:
        out.append(f"**{s['n']}.** {s['text']}")
        if s.get("lit"):
            out.append(f"- *Lit.* {s['lit']}")
        if s.get("nat"):
            out.append(f"- *Nat.* {s['nat']}")
        out.append("")

    if lesson.get("notes"):
        out += [f"## {h.notes} — {lesson.get('focus', '')}", ""]
        for note in lesson["notes"]:
            out.append(f"**{note.get('point', '')}** — *{note.get('example', '')}*")
            out.append(note.get("rule", ""))
            if note.get("mistake"):
                out.append(f"Typical {native.english_name}-speaker mistake: {note['mistake']}")
            out.append("")

    if lesson.get("vocab"):
        out += [f"## {h.vocabulary}", "",
                f"| {target.endonym} | {h.literal} | {native.endonym} | {h.note} |",
                "|---|---|---|---|"]
        for v in lesson["vocab"]:
            out.append(
                f"| {v.get('word', '')} | {v.get('literal', '')} | "
                f"{v.get('native', '')} | {v.get('note', '')} |"
            )
        out.append("")

    if lesson.get("speaking_prompts"):
        out += [f"## {h.speaking}", ""]
        for i, prompt in enumerate(lesson["speaking_prompts"], start=1):
            out.append(f"{i}. {prompt}")
        out.append("")

    return "\n".join(out)


def _direction_label(lesson: dict) -> str:
    """"Español → English" — which way this lesson is translated, in the two
    languages' own names."""
    source, spoken = ((lesson["native"], lesson["target"])
                      if direction_of(lesson) == INTO_TARGET
                      else (lesson["target"], lesson["native"]))
    return f"{languages.get(source).endonym} → {languages.get(spoken).endonym}"


def _labels(lesson: dict) -> languages.ReviewLabels:
    """The review's scaffolding, in the language the student reads it in."""
    return languages.get(lesson["native"]).review_labels


def _verdict_word(labels: languages.ReviewLabels, verdict: str) -> str:
    return {"missed": labels.missed, "blurred": labels.blurred,
            "check": labels.check}[verdict]


def _heard_sentences(lesson: dict) -> dict[int, str]:
    """Sentence number → what the student heard, for a reverse challenge. The
    finding quotes back the target-language sentence they were aiming for, and
    on its own that reads like a source text they never saw."""
    if direction_of(lesson) != INTO_TARGET:
        return {}
    return {s["n"]: (s.get("nat") or s.get("lit") or "").strip()
            for s in lesson.get("sentences", [])}


def render_review_markdown(review: dict, lesson: dict) -> str:
    """The review in the translation-review skill's layout: what to fix, what
    to check, what held up."""
    lb = _labels(lesson)
    reverse = direction_of(lesson) == INTO_TARGET
    heard = _heard_sentences(lesson)
    # The quoted target-language line is the story the student read in one
    # direction and the rendering they were aiming for in the other.
    target_label = lb.model_answer if reverse else languages.name(lesson["target"])
    out = [f"# {lb.heading} — {lesson['title']}", "", review.get("summary", ""), ""]

    to_fix = findings_by_verdict(review, "missed", "blurred")
    if to_fix:
        out += [f"## {lb.what_to_fix}", ""]
        for f in to_fix:
            out.append(f"**{lb.sentence} {f.get('sentence', '?')} — "
                       f"{_verdict_word(lb, f['verdict'])}**")
            out.append(f"> {lb.you_said}: \"{f.get('said', '')}\"")
            if heard.get(f.get("sentence")):
                out.append(f"> {lb.source}: {heard[f['sentence']]}")
            out.append(f"> {target_label}: *{f.get('source', '')}*")
            if f.get("literal"):
                out.append(f"> {lb.literal}: *{f['literal']}*")
            out.append("")
            out.append(f.get("explanation", ""))
            if f.get("correction"):
                out.append(f"**{lb.better}:** {f['correction']}")
            out.append("")

    checks = findings_by_verdict(review, "check")
    if checks:
        out += [f"## {lb.worth_checking}", ""]
        for f in checks:
            out.append(f"**{lb.sentence} {f.get('sentence', '?')}** — "
                       f"{f.get('correction') or f.get('explanation', '')}")
            out.append("")

    if review.get("holding_up"):
        out += [f"## {lb.holding_up}", ""]
        out += [f"- {item}" for item in review["holding_up"]]
        out.append("")

    return "\n".join(out)


def review_chat_text(review: dict, lesson: dict) -> str:
    """A plain-text rendering for the chat bubble. The bubble shows text, not
    markdown, so the file's `**bold**` and blockquotes would only be noise."""
    lb = _labels(lesson)
    heard = _heard_sentences(lesson)
    lines = [review.get("summary", "").strip()]

    for f in findings_by_verdict(review, "missed", "blurred"):
        lines.append("")
        lines.append(f"{lb.sentence} {f.get('sentence', '?')} "
                     f"({_verdict_word(lb, f['verdict'])}): "
                     f"{heard.get(f.get('sentence')) or f.get('source', '')}")
        lines.append(f"{lb.you_said}: {f.get('said', '')}")
        if heard.get(f.get("sentence")):
            lines.append(f"{lb.model_answer}: {f.get('source', '')}")
        if f.get("explanation"):
            lines.append(f.get("explanation"))
        if f.get("correction"):
            lines.append(f"{lb.better}: {f['correction']}")

    checks = findings_by_verdict(review, "check")
    if checks:
        lines.append("")
        lines.append(f"{lb.worth_checking}:")
        for f in checks:
            lines.append(f"- {lb.sentence} {f.get('sentence', '?')}: "
                         f"{f.get('correction') or f.get('explanation', '')}")

    if review.get("holding_up"):
        lines.append("")
        lines.append(f"{lb.holding_up}:")
        lines += [f"- {item}" for item in review["holding_up"]]

    return "\n".join(line for line in lines if line is not None).strip()


def spoken_review_summary(review: dict, lesson: dict) -> str:
    """What the tutor says out loud after marking. Kokoro reads this, so it
    stays short and skips the sentence-by-sentence detail that is easier to
    read than to hear. Spoken in the student's native language, since the point
    is that the explanation lands."""
    lb = _labels(lesson)
    parts = [review.get("summary", "").strip()]
    to_fix = findings_by_verdict(review, "missed", "blurred")
    checks = findings_by_verdict(review, "check")

    if to_fix:
        first = to_fix[0]
        parts.append(lb.spoken_main.format(n=first.get("sentence", "?")))
        parts.append(first.get("explanation", ""))
        if len(to_fix) > 1:
            parts.append(lb.spoken_more.format(n=len(to_fix) - 1))
    elif not checks:
        parts.append(lb.spoken_clean)

    if checks:
        parts.append(lb.spoken_question.format(
            question=checks[0].get("correction") or checks[0].get("explanation", "")))

    parts.append(lb.spoken_invite)
    return " ".join(p for p in parts if p)
