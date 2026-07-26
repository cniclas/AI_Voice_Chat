"""Read the two Claude skills' reference material at runtime.

`.claude/skills/language-lesson/` and `.claude/skills/translation-review/` are
written for Claude, but the browser's translation-challenge mode has to do the
same two jobs against the local Ollama model. Rather than paraphrasing those
documents into a second set of prompts that quietly drifts out of date, this
module pulls the exact sections the skills tell a reader to consult — the CEFR
band for the level, the forms the target language allows at that level, the
focus pattern being drilled, the glossing notation, the judging filters — and
`translation_challenge.py` pastes them into the Ollama prompts.

Two axes decide which file is read. The **target** language alone determines
its form inventory, focus patterns and glossing notes
(`language-lesson/references/languages/<target>.md`). The **pair** determines
what a reader of one language misreads in the other, which is directional and
so lives in `translation-review/references/languages/<target>-<native>.md` —
the traps a Spanish speaker falls into reading French are not the ones an
English speaker falls into reading Spanish.

The skill files stay the single source of truth: editing
`references/languages/fr.md` changes what the app generates, with no code
change. Only whole sections are pulled, because a small model degrades quickly
if handed 10 kB of prose it mostly doesn't need.

Everything degrades to an empty string if a file or section is missing, so the
pipeline still runs (with weaker prompts) on a checkout without the skills.
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import curriculum
import languages

_ROOT = Path(__file__).resolve().parent
SKILLS_ROOT = _ROOT / ".claude" / "skills"

LESSON_SKILL = SKILLS_ROOT / "language-lesson"
REVIEW_SKILL = SKILLS_ROOT / "translation-review"

DEFAULT_LEVEL = curriculum.DEFAULT_LEVEL

# Judging sections worth spending prompt budget on: the filters that decide
# whether a deviation is a finding at all. The narrative framing around them is
# for a human reader and would only dilute the instruction.
_JUDGING_SECTIONS = (
    "The discriminator that works",
    "Never flag these",
    "Signals it is the learner",
    "Paraphrase is not error",
    "The three verdicts",
    "Weighting by lesson focus",
    "Don't manufacture findings",
)

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")


@dataclass(frozen=True)
class _Section:
    level: int
    title: str
    parents: tuple[str, ...]
    body: str


def _normalize(text: str) -> str:
    """Casefold, strip accents and markdown noise, so a heading can be matched
    however it was typed ("pretérito" vs "preterito", "`se`" vs "se")."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).strip()


@lru_cache(maxsize=None)
def _sections(path: str) -> tuple[_Section, ...]:
    """Split a markdown file into sections, each carrying the chain of headings
    above it so `### B1` under `## Form inventory` is addressable."""
    file = Path(path)
    if not file.is_file():
        return ()

    sections: list[_Section] = []
    stack: list[str] = []  # heading titles by depth, index = level - 1
    current: list[str] = []
    level = title = None

    def flush():
        if title is not None:
            sections.append(_Section(
                level=level,
                title=title,
                parents=tuple(stack[: level - 1]),
                body="\n".join(current).strip(),
            ))

    for line in file.read_text(encoding="utf-8").splitlines():
        match = _HEADING.match(line)
        if not match:
            current.append(line)
            continue
        flush()
        level = len(match.group(1))
        title = match.group(2).strip()
        current = []
        stack[level - 1:] = [title]
    flush()
    return tuple(sections)


def _section_body(path: Path, title_prefix: str, parent: str | None = None) -> str:
    """Body of the first section whose title starts with `title_prefix` (and,
    when given, that sits under a heading starting with `parent`)."""
    wanted = _normalize(title_prefix)
    wanted_parent = _normalize(parent) if parent else None
    for section in _sections(str(path)):
        if not _normalize(section.title).startswith(wanted):
            continue
        if wanted_parent and not any(_normalize(p).startswith(wanted_parent) for p in section.parents):
            continue
        return section.body
    return ""


def _child_titles(path: Path, parent: str) -> list[str]:
    wanted = _normalize(parent)
    return [
        s.title for s in _sections(str(path))
        if s.parents and _normalize(s.parents[-1]).startswith(wanted)
    ]


# ---------------------------------------------------------------------------
# language-lesson: how to write and gloss the story
# ---------------------------------------------------------------------------

def lesson_guidance(title_prefix: str) -> str:
    """A section of the language-lesson SKILL.md itself — "Writing the story",
    "The aligned translation" (which carries the glossing notation and its
    worked examples), "Focus notes and vocabulary"."""
    return _section_body(LESSON_SKILL / "SKILL.md", title_prefix)


def level_band(level: str = DEFAULT_LEVEL) -> str:
    """The language-neutral CEFR band: text budget, clause complexity, and the
    functions a reader at this level can handle."""
    return _section_body(LESSON_SKILL / "references" / "levels.md", level)


def _target_file(target: str) -> Path:
    return LESSON_SKILL / "references" / "languages" / f"{target}.md"


def _pair_file(target: str, native: str) -> Path:
    return REVIEW_SKILL / "references" / "languages" / f"{target}-{native}.md"


def language_forms(level: str, target: str) -> str:
    """The forms that level admits in one language — and, just as importantly,
    the "out" list, since level drift happens by importing from the row below."""
    return _section_body(_target_file(target), level, parent="Form inventory")


def focus_names(target: str) -> list[str]:
    """Every grammar focus the language file documents a pattern for."""
    return _child_titles(_target_file(target), "Focus patterns")


def focus_pattern(focus: str, target: str) -> str:
    """What the story must do structurally for this focus, the mistake a
    learner reliably makes, and how to gloss it."""
    return _section_body(_target_file(target), focus, parent="Focus patterns")


def glossing_notes(target: str, native: str) -> str:
    """Glossing quirks — dropped subjects, clitic order, idioms that must stay
    literal — plus the worked examples for glossing into this native language.

    A gloss is directional even though the file is per-target: the same French
    partitive needs saying one way to a Spanish reader and another way to an
    English one. So the shared body is returned together with the
    `### Into <native>` subsection, when the file documents that native
    language.
    """
    path = _target_file(target)
    shared = _section_body(path, "Glossing notes")
    into = _section_body(path, f"Into {languages.name(native)}", parent="Glossing notes")
    return "\n\n".join(part for part in (shared, into) if part)


def pick_focus(profile: dict, target: str) -> str:
    """Choose the grammar focus for today's lesson from the student's profile.

    An active focus goal wins outright: the student (with the progress-review
    skill) decided what to work on, and that intention should outrank whatever
    the last analysis pass happened to flag hardest. Only the goal's focus name
    is trusted, and only when this language documents a pattern for it — a goal
    naming something `focus_pattern()` can't resolve would otherwise degrade the
    lesson prompt silently.

    Failing that, the skill's own instruction is to "pull the top recurring
    weakness from the student profile"; matching is done here rather than by the
    LLM so the same profile always yields the same focus. Weakness topics are
    free text written by an earlier analysis pass ("preterite vs imperfect"), so
    tokens are compared by stem — enough to bridge the English and target-language
    spellings of the same grammatical term without pulling in a stemmer.

    Returns "" when the target language has no reference file yet, so the caller
    omits the focus block rather than drilling a pattern from another language.
    """
    names = focus_names(target)
    if not names:
        return ""

    goal = curriculum.goal_focus(profile)
    if goal:
        wanted = _normalize(goal)
        for name in names:
            if _normalize(name) == wanted:
                return name

    tokenized = [(name, set(_normalize(name).split())) for name in names]
    ranked = sorted(profile.get("weaknesses", []),
                    key=lambda w: w.get("occurrences", 0), reverse=True)

    for weakness in ranked:
        topic_tokens = {t for t in _normalize(weakness.get("topic", "")).split() if len(t) >= 4}
        if not topic_tokens:
            continue
        best, best_score = None, 0
        for name, name_tokens in tokenized:
            score = sum(
                any(t.startswith(n[:5]) or n.startswith(t[:5]) for n in name_tokens)
                for t in topic_tokens
            )
            if score > best_score:
                best, best_score = name, score
        if best:
            return best

    # No profile signal yet: take the language file's first focus, which is
    # written to be the one narration at this level lives or dies on.
    return names[0]


# ---------------------------------------------------------------------------
# translation-review: how to judge the spoken translation
# ---------------------------------------------------------------------------

def judging_rules() -> str:
    """The language-neutral filters: what is transcription noise, what is a
    real finding, and the three verdicts."""
    path = REVIEW_SKILL / "references" / "judging.md"
    parts = [(title, _section_body(path, title)) for title in _JUDGING_SECTIONS]
    return "\n\n".join(f"## {title}\n{body}" for title, body in parts if body)


def comprehension_traps(target: str, native: str) -> str:
    """The trap catalogue for reading one language into another — false
    friends, clitic misreadings, mood and aspect flattening. Directional: what
    trips up a native speaker of `native` reading `target`."""
    path = _pair_file(target, native)
    parts = [
        (s.title, s.body) for s in _sections(str(path))
        # "Adding another pair" is instruction for whoever writes the next pair
        # file, not material for marking today's translation.
        if s.level == 2 and not _normalize(s.title).startswith("adding another")
    ]
    return "\n\n".join(f"## {title}\n{body}" for title, body in parts if body)
