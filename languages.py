"""Registry of the languages this app can teach, speak and transcribe.

Everything language-shaped that the code (as opposed to the skills' reference
material) needs to know lives here, so adding a language is one entry rather
than a grep across ten files: the name to paste into an English prompt, the
name to put on a button, the push-to-talk key, the one-line reminder that keeps
the tutor answering in the right language, the Kokoro voice, and the section
headings a generated lesson is rendered with.

What deliberately does *not* live here is anything a lesson's quality depends
on — form inventories, focus patterns, glossing quirks, comprehension traps.
Those stay in `.claude/skills/**/references/` and are read at runtime by
`skill_refs.py`, so editing a markdown file changes what the app generates.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LessonHeadings:
    """Section headings for a lesson rendered *in* the target language.

    `language-lesson/SKILL.md` tells Claude to write the lesson's headings in
    the target language; `translation_challenge.render_lesson_markdown()` has
    to produce the same document from the local model, so it needs the same
    words. Column headers for the vocabulary table are built from the two
    languages' endonyms plus `literal` and `note`.
    """
    level: str
    focus: str
    story: str
    aligned: str
    notes: str
    vocabulary: str
    speaking: str
    literal: str
    note: str


@dataclass(frozen=True)
class ReviewLabels:
    """Scaffolding for a marked translation, written in the *native* language.

    The review is the one artifact the student reads and hears entirely in the
    language they already have — `spoken_review_summary()` is synthesized with
    the native voice — so English section headings around Spanish prose would
    be both ugly and, read aloud, mispronounced.
    """
    heading: str          # "Translation review"
    what_to_fix: str
    worth_checking: str
    holding_up: str
    sentence: str         # "Sentence" / "Frase" / "Phrase"
    you_said: str
    source: str           # label for the original-language line
    literal: str
    better: str
    missed: str
    blurred: str
    check: str            # the "worth checking" verdict, inline
    # What the tutor says out loud after marking. These are glue around prose
    # the model wrote in this same language, and Kokoro reads the result with
    # this language's voice, so they cannot stay English.
    spoken_main: str      # takes {n}
    spoken_more: str      # takes {n}
    spoken_clean: str
    spoken_question: str  # takes {question}
    spoken_invite: str


@dataclass(frozen=True)
class Language:
    code: str            # ISO code — also what Whisper and the wire format use
    english_name: str    # for the English prompt templates
    endonym: str         # for buttons and headings the student reads
    record_key: str      # terminal push-to-talk key
    reply_reminder: str  # one line, written IN this language (see query_llm)
    lesson_headings: LessonHeadings
    review_labels: ReviewLabels
    tts: tuple[str, str] | None = None  # Kokoro (lang_code, voice), if any

    @property
    def has_voice(self) -> bool:
        return self.tts is not None


LANGUAGES: dict[str, Language] = {
    "en": Language(
        code="en",
        english_name="English",
        endonym="English",
        record_key="e",
        reply_reminder="The student's last message is in English. Reply in English.",
        lesson_headings=LessonHeadings(
            level="Level", focus="Focus", story="Story",
            aligned="Aligned reading", notes="Grammar notes",
            vocabulary="Vocabulary", speaking="To talk about",
            literal="Literal", note="Note",
        ),
        review_labels=ReviewLabels(
            heading="Translation review", what_to_fix="What to fix",
            worth_checking="Worth checking", holding_up="Holding up well",
            sentence="Sentence", you_said="You said", source="Source",
            literal="Literal", better="Better",
            missed="missed", blurred="blurred", check="worth checking",
            spoken_main="The main thing to look at is sentence {n}.",
            spoken_more="There are {n} more notes written up for you.",
            spoken_clean="Nothing to correct — that was a solid reading.",
            spoken_question="One question: {question}",
            spoken_invite="Ask me anything about the story, or we can just keep talking.",
        ),
        # af_heart is Kokoro's top-graded English voice.
        tts=("a", "af_heart"),
    ),
    "es": Language(
        code="es",
        english_name="Spanish",
        endonym="Español",
        record_key="s",
        reply_reminder=(
            "El último mensaje del estudiante está en español. "
            "Responde en español."
        ),
        lesson_headings=LessonHeadings(
            level="Nivel", focus="Enfoque", story="Historia",
            aligned="Lectura alineada", notes="Notas de gramática",
            vocabulary="Vocabulario", speaking="Para hablar",
            literal="Literal", note="Nota",
        ),
        review_labels=ReviewLabels(
            heading="Corrección de la traducción", what_to_fix="Qué corregir",
            worth_checking="Para comprobar", holding_up="Lo que va bien",
            sentence="Frase", you_said="Dijiste", source="Original",
            literal="Literal", better="Mejor",
            missed="error de sentido", blurred="matiz perdido",
            check="para comprobar",
            spoken_main="Lo principal está en la frase {n}.",
            spoken_more="Hay {n} notas más escritas para ti.",
            spoken_clean="No hay nada que corregir; fue una lectura sólida.",
            spoken_question="Una pregunta: {question}",
            spoken_invite="Pregúntame lo que quieras sobre la historia, o seguimos charlando.",
        ),
        # ef_dora is Kokoro's only female Spanish voice.
        tts=("e", "ef_dora"),
    ),
    "fr": Language(
        code="fr",
        english_name="French",
        endonym="Français",
        record_key="f",
        reply_reminder=(
            "Le dernier message de l'étudiant est en français. "
            "Réponds en français."
        ),
        lesson_headings=LessonHeadings(
            level="Niveau", focus="Objectif", story="Histoire",
            aligned="Lecture alignée", notes="Notes de grammaire",
            vocabulary="Vocabulaire", speaking="Pour parler",
            literal="Littéral", note="Note",
        ),
        review_labels=ReviewLabels(
            heading="Correction de la traduction", what_to_fix="À corriger",
            worth_checking="À vérifier", holding_up="Ce qui tient bien",
            sentence="Phrase", you_said="Tu as dit", source="Original",
            literal="Littéral", better="Mieux",
            missed="contresens", blurred="nuance perdue", check="à vérifier",
            spoken_main="L'essentiel se trouve dans la phrase {n}.",
            spoken_more="Il y a {n} autres remarques écrites pour toi.",
            spoken_clean="Rien à corriger, c'était une lecture solide.",
            spoken_question="Une question : {question}",
            spoken_invite="Pose-moi toutes tes questions sur l'histoire, ou continuons à discuter.",
        ),
        # ff_siwis is Kokoro-82M's only French voice.
        tts=("f", "ff_siwis"),
    ),
}


def get(code: str) -> Language:
    """The language for an ISO code, or a clear error naming what is supported.

    Language codes arrive from the browser (`user_audio.language`) and from the
    per-user config, so a bad one should say what went wrong rather than
    surfacing as a `KeyError` three frames deeper.
    """
    try:
        return LANGUAGES[code]
    except KeyError:
        raise ValueError(
            f"Unsupported language {code!r}; known: {', '.join(sorted(LANGUAGES))}"
        ) from None


def name(code: str) -> str:
    """English name, for interpolation into a prompt template."""
    return get(code).english_name


def codes() -> list[str]:
    return list(LANGUAGES)
