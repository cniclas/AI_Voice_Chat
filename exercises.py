"""Content and grading for the non-story conversation modes:

- Homework lesson: load the latest homework.md (or build a lesson from the
  student profile) and the system prompt that makes the tutor drive it.
- Flashcards: LLM-generated English→Spanish word cards, graded by an LLM
  judge that accepts synonyms and transcription artifacts.
- Fill-in-the-blanks: LLM-generated cloze sentences, graded the same way.

All judges fall back to a naive normalized string match if Ollama is
unreachable, so an exercise session never dies mid-deck.
"""

import re
import unicodedata
from pathlib import Path

import requests

from curriculum import (
    chat_completion,
    chat_completion_json,
    top_vocab_to_practice,
    top_weaknesses,
)
from session_core import RECORDINGS_ROOT

FLASHCARD_COUNT = 8
FILL_BLANK_COUNT = 6


# ---------------------------------------------------------------------------
# Homework lesson mode
# ---------------------------------------------------------------------------

HOMEWORK_SYSTEM_PROMPT_TEMPLATE = (
    "Eres un profesor de español amable y paciente dirigiendo una lección oral "
    "basada en los deberes del estudiante. El estudiante puede hablar en inglés "
    "o en español; responde SIEMPRE en el mismo idioma que usó el estudiante en "
    "su último mensaje (se te indicará antes de cada mensaje). Habla con frases "
    "cortas porque tus respuestas se leerán en voz alta. Trabaja los deberes "
    "ejercicio por ejercicio: presenta UN ejercicio, espera la respuesta del "
    "estudiante, corrige brevemente si hace falta y pasa al siguiente. Tú "
    "diriges el ritmo de la lección.\n\n"
    "DEBERES DE HOY:\n{lesson}"
)

HOMEWORK_OPENING_INSTRUCTION = (
    "(El estudiante está listo. Saluda muy brevemente en español y presenta el "
    "primer ejercicio de los deberes.)"
)


def build_homework_system_prompt(lesson_text: str) -> str:
    return HOMEWORK_SYSTEM_PROMPT_TEMPLATE.format(lesson=lesson_text)


def homework_opening_turn(history: list) -> str:
    """Ask the tutor to open the lesson (greeting + first exercise). The
    synthetic instruction is not stored in `history`, only the reply."""
    messages = history + [{"role": "user", "content": HOMEWORK_OPENING_INSTRUCTION}]
    text = chat_completion(messages)
    history.append({"role": "assistant", "content": text})
    return text


def find_latest_homework() -> str | None:
    """Return the text of the most recent recordings/*/homework.md, or None."""
    root = Path(RECORDINGS_ROOT)
    if not root.exists():
        return None
    for session_dir in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True):
        homework = session_dir / "homework.md"
        if homework.is_file():
            try:
                return homework.read_text(encoding="utf-8")
            except OSError:
                continue
    return None


def lesson_from_profile(profile: dict) -> str:
    """Deterministic fallback lesson when no homework.md exists yet, built
    from the profile's recurring weaknesses and practice vocabulary."""
    lines = ["(No saved homework found — today's lesson reviews the student's profile.)", ""]
    weaknesses = top_weaknesses(profile, n=3)
    vocab = top_vocab_to_practice(profile, n=8)
    if weaknesses:
        lines.append("Focus points:")
        lines.extend(f"- {w.get('topic', '')} ({w.get('type', '')}): {w.get('explanation', '')}"
                     for w in weaknesses)
        lines.append("")
    if vocab:
        lines.append("Vocabulary to practice: " + ", ".join(vocab))
        lines.append("")
    if not weaknesses and not vocab:
        lines.append("General B1 practice: ser vs estar, pretérito vs imperfecto, "
                     "and everyday vocabulary (daily routines, travel, food).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Flashcards
# ---------------------------------------------------------------------------

FLASHCARD_GEN_SYSTEM = (
    "You create Spanish vocabulary flashcards for an intermediate (B1) student. "
    "Respond with JSON only."
)

FLASHCARD_GEN_USER_TEMPLATE = (
    "Create exactly {n} flashcards. Each card has 'prompt' — a short English "
    "word or expression shown to the student — and 'answer' — the Spanish word "
    "or expression the student should say out loud.\n"
    "Use these practice words as answers first (one card each): {practice}. "
    "Fill any remaining cards with common, useful B1-level Spanish vocabulary. "
    "No duplicate answers.\n"
    'Return JSON: {{"cards": [{{"prompt": "<English>", "answer": "<Spanish>"}}]}}'
)

FLASHCARD_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["prompt", "answer"],
            },
        },
    },
    "required": ["cards"],
}


def generate_flashcards(profile: dict, n: int = FLASHCARD_COUNT) -> list[dict]:
    """Returns [{"prompt": English, "answer": Spanish}, ...]. Raises
    requests.RequestException/ValueError upward on failure."""
    practice = top_vocab_to_practice(profile, n=n)
    messages = [
        {"role": "system", "content": FLASHCARD_GEN_SYSTEM},
        {"role": "user", "content": FLASHCARD_GEN_USER_TEMPLATE.format(
            n=n, practice=", ".join(practice) or "(none)")},
    ]
    data = chat_completion_json(messages, FLASHCARD_SCHEMA)

    cards, seen = [], set()
    for c in data.get("cards", []):
        prompt = (c.get("prompt") or "").strip()
        answer = (c.get("answer") or "").strip()
        key = answer.casefold()
        if not prompt or not answer or key in seen:
            continue
        seen.add(key)
        cards.append({"prompt": prompt, "answer": answer})
    if not cards:
        raise ValueError("no usable flashcards generated")
    return cards[:n]


# ---------------------------------------------------------------------------
# Fill in the blanks
# ---------------------------------------------------------------------------

FILL_BLANK_GEN_SYSTEM = (
    "You create Spanish fill-in-the-blank exercises for an intermediate (B1) "
    "student. Respond with JSON only."
)

FILL_BLANK_GEN_USER_TEMPLATE = (
    "Create exactly {n} short Spanish sentences, each with EXACTLY ONE missing "
    "word (or short expression) replaced by ___ (three underscores). For each "
    "item give: 'sentence' — the sentence containing ___; 'answer' — the "
    "missing Spanish word(s); 'hint' — an English translation of the full "
    "sentence.\n"
    "Target these recurring weaknesses: {weaknesses}. Work in these practice "
    "words where natural: {practice}.\n"
    'Return JSON: {{"items": [{{"sentence": "...", "answer": "...", "hint": "..."}}]}}'
)

FILL_BLANK_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence": {"type": "string"},
                    "answer": {"type": "string"},
                    "hint": {"type": "string"},
                },
                "required": ["sentence", "answer", "hint"],
            },
        },
    },
    "required": ["items"],
}


def generate_fill_blanks(profile: dict, n: int = FILL_BLANK_COUNT) -> list[dict]:
    """Returns [{"sentence": ..., "answer": ..., "hint": ...}, ...]. Raises
    requests.RequestException/ValueError upward on failure."""
    weaknesses = ", ".join(w.get("topic", "") for w in top_weaknesses(profile, n=3)) or "(none)"
    practice = ", ".join(top_vocab_to_practice(profile, n=n)) or "(none)"
    messages = [
        {"role": "system", "content": FILL_BLANK_GEN_SYSTEM},
        {"role": "user", "content": FILL_BLANK_GEN_USER_TEMPLATE.format(
            n=n, weaknesses=weaknesses, practice=practice)},
    ]
    data = chat_completion_json(messages, FILL_BLANK_SCHEMA)

    items = []
    for it in data.get("items", []):
        sentence = (it.get("sentence") or "").strip()
        answer = (it.get("answer") or "").strip()
        if not sentence or not answer or "___" not in sentence:
            continue
        items.append({"sentence": sentence, "answer": answer,
                      "hint": (it.get("hint") or "").strip()})
    if not items:
        raise ValueError("no usable fill-in-the-blank items generated")
    return items[:n]


# ---------------------------------------------------------------------------
# LLM judge (with offline fallback)
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = (
    "You are grading a Spanish speaking exercise for an intermediate (B1) "
    "student. You get the expected answer and a speech-to-text transcription "
    "of what the student said. Grade meaning, not spelling: accept correct "
    "synonyms, missing or wrong accents, minor transcription artifacts "
    "(punctuation, capitalization), and an added article (el/la/un/una). "
    "Reject answers in the wrong language or with a clearly different meaning. "
    "Respond with JSON only."
)

FLASHCARD_JUDGE_USER_TEMPLATE = (
    'English prompt shown to the student: "{prompt}"\n'
    'Expected Spanish answer: "{expected}"\n'
    'The student said: "{heard}"\n\n'
    "Did the student say the expected word or an acceptable synonym?\n"
    'Return JSON: {{"correct": true|false, "feedback": "<one short sentence in English>"}}'
)

FILL_BLANK_JUDGE_USER_TEMPLATE = (
    'Sentence with a blank: "{sentence}"\n'
    'Expected word(s) for the blank: "{expected}"\n'
    'English meaning of the sentence: "{hint}"\n'
    'The student said: "{heard}"\n\n'
    "The student may say just the missing word(s) or the whole sentence. "
    "Accept any answer that fills the blank grammatically with the intended "
    "meaning (synonyms are fine).\n"
    'Return JSON: {{"correct": true|false, "feedback": "<one short sentence in English>"}}'
)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "correct": {"type": "boolean"},
        "feedback": {"type": "string"},
    },
    "required": ["correct", "feedback"],
}

_ARTICLES = {"el", "la", "los", "las", "un", "una", "unos", "unas"}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t not in _ARTICLES]
    return " ".join(tokens)


def _naive_match(expected: str, heard: str) -> bool:
    e, h = _normalize(expected), _normalize(heard)
    return bool(e) and (e == h or f" {e} " in f" {h} ")


def _judge(user_prompt: str, expected: str, heard: str) -> dict:
    """Run the LLM judge; fall back to a normalized string match if Ollama is
    down or returns garbage. Always returns {"correct": bool, "feedback": str}."""
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    try:
        data = chat_completion_json(messages, JUDGE_SCHEMA)
        return {"correct": bool(data["correct"]), "feedback": str(data.get("feedback", ""))}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        correct = _naive_match(expected, heard)
        return {"correct": correct,
                "feedback": "(Graded offline by exact match — the LLM judge was unavailable.)"}


def judge_flashcard(prompt: str, expected: str, heard: str) -> dict:
    return _judge(
        FLASHCARD_JUDGE_USER_TEMPLATE.format(prompt=prompt, expected=expected, heard=heard),
        expected, heard)


def judge_fill_blank(sentence: str, expected: str, hint: str, heard: str) -> dict:
    return _judge(
        FILL_BLANK_JUDGE_USER_TEMPLATE.format(
            sentence=sentence, expected=expected, hint=hint, heard=heard),
        expected, heard)


# ---------------------------------------------------------------------------
# Results persistence
# ---------------------------------------------------------------------------

def save_exercise_results(session_dir: Path, title: str, results: list[dict]) -> Path:
    """Write results.md for a completed exercise session. Each result is
    {"item": dict, "heard": str, "correct": bool, "feedback": str}."""
    n_correct = sum(1 for r in results if r["correct"])
    lines = [f"# {title} — {session_dir.name}", "",
             f"**Score:** {n_correct}/{len(results)}", ""]
    for i, r in enumerate(results, start=1):
        item = r["item"]
        shown = item.get("prompt") or item.get("sentence", "")
        mark = "✓" if r["correct"] else "✗"
        lines.append(f"{i}. {mark} **{shown}** — expected *{item['answer']}*, "
                     f"heard “{r['heard']}”. {r.get('feedback', '')}")
    path = session_dir / "results.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
