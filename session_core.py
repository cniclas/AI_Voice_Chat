"""Shared session-orchestration helpers used by both the CLI (`main.py`) and
the browser UI (`web/session.py`): transcription, LLM turn-taking, transcript
persistence, and the system prompts that frame a conversation."""

import os
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

import languages
from curriculum import chat_completion

_ROOT = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_ROOT = os.path.join(_ROOT, "recordings")

# Create the root recordings directory if it doesn't exist
Path(RECORDINGS_ROOT).mkdir(exist_ok=True)


@dataclass
class Response:
    """Represents a single exchange in the conversation."""
    author: str  # "user" or "assistant"
    language: str  # "en", "es", etc.
    text: str
    timestamp: datetime  # When this turn occurred
    audio_sample: Optional[str] = None  # Path to audio file


# The prompts are written in English and parameterised by language name rather
# than written once per language: a French copy of every prompt would drift out
# of step with the Spanish one within a couple of edits. What stays written *in*
# the language is the per-turn reminder (`Language.reply_reminder`), which is
# the instruction the local model is most likely to actually obey.
BASE_SYSTEM_PROMPT_TEMPLATE = (
    "You are a warm, patient {target_name} tutor talking with a student whose "
    "native language is {native_name}. The student may speak in {native_name} or "
    "in {target_name}; ALWAYS reply in the same language they used in their last "
    "message (you are told which before each one). Never answer in a third "
    "language. Speak naturally and conversationally, in short sentences, because "
    "your replies are read aloud. Keep the conversation moving and ask questions "
    "so the student keeps talking. Do not correct mistakes mid-conversation; "
    "corrections happen at the end of the session."
)

STORY_SYSTEM_PROMPT_TEMPLATE = BASE_SYSTEM_PROMPT_TEMPLATE + (
    "\n\nToday's conversation is about a story the student has just listened to, "
    "based on a Wikipedia article. Ask about the story, its subject and what the "
    "student thinks. Use this context:\n\n"
    "ARTICLE ({article_title}):\n{article_extract}\n\n"
    "STORY:\n{story}"
)

CHALLENGE_SYSTEM_PROMPT_TEMPLATE = BASE_SYSTEM_PROMPT_TEMPLATE + (
    "\n\nThe student has just done a translation challenge: they listened to a "
    "story in {target_name} and translated it aloud into {native_name}, their "
    "native language. They already have the marked review. Now they can ask "
    "anything about the story, about one sentence, or about their mistakes; "
    "answer with examples from the story itself, quoting the sentence you are "
    "talking about. Today's grammar focus is: {focus}.\n\n"
    "STORY ({story_title}):\n{story}\n\n"
    "{review_context}"
)

REVIEW_CONTEXT_TEMPLATE = (
    "THE REVIEW OF THEIR TRANSLATION:\n{summary}\n{findings}\n\n"
    "Do not repeat the whole review; the student already has it in front of them."
)

# The tutor is told what the student is working toward, not told to drill it.
# Corrections mid-conversation are explicitly off the table in the base prompt
# (they happen in the analysis phase), so the only useful thing a goal can do
# here is shape the questions asked — steering the student into situations that
# need the structure is what produces evidence of whether they have it.
GOAL_CONTEXT_TEMPLATE = (
    "\n\nThe student is working specifically on: {goal}. Steer your questions "
    "toward situations that need that structure, naturally. Still do not correct "
    "mistakes mid-conversation."
)

CONTEXT_EXTRACT_CHARS = 1200  # article extract length embedded in the system prompt


WHISPER_SAMPLE_RATE = 16000  # Whisper expects 16 kHz mono float32


def load_audio_16k(path: str) -> np.ndarray:
    """Decode a PCM WAV to a mono 16 kHz float32 array without needing ffmpeg.

    Whisper normally shells out to the ffmpeg CLI to decode/resample audio.
    We record standard 16-bit PCM WAVs, so we can decode them in-process and
    hand Whisper the array directly, keeping the app self-contained.
    """
    with wave.open(path, "rb") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())

    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1)

    if sample_rate != WHISPER_SAMPLE_RATE and audio.size:
        target_len = int(round(audio.shape[0] * WHISPER_SAMPLE_RATE / sample_rate))
        x_old = np.linspace(0.0, 1.0, num=audio.shape[0], endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        audio = np.interp(x_new, x_old, audio).astype(np.float32)

    return np.ascontiguousarray(audio, dtype=np.float32)


def transcribe_audio(audio_path: str, model, language: str = "en") -> str:
    audio = load_audio_16k(audio_path)
    result = model.transcribe(audio, language=language)
    return result["text"].strip()


def create_session_dir(recordings_dir: Path) -> Path:
    """Create a new date/time-named folder for this conversation."""
    name = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    session_dir = Path(recordings_dir) / name
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def generate_audio_filename(session_dir: Path, author: str, language: str) -> str:
    """Generate a unique audio filename inside the session folder."""
    timestamp = datetime.now().strftime("%H%M%S_%f")[:-3]  # Include milliseconds
    return str(session_dir / f"{author}_{language}_{timestamp}.wav")


def query_llm(user_text: str, language: str, history: list) -> str:
    """Append the user turn, get a reply, and record it in the history.

    A per-turn language reminder is injected into the outgoing message list
    (never stored in `history`) so the tutor mirrors whichever language the
    student just spoke, instead of settling into one of them. The reminder is
    written in that language, which the model follows more reliably than an
    English instruction about it.
    """
    history.append({"role": "user", "content": user_text})
    messages = history[:-1] + [
        {"role": "system", "content": languages.get(language).reply_reminder},
        history[-1],
    ]
    assistant_text = chat_completion(messages)
    history.append({"role": "assistant", "content": assistant_text})
    return assistant_text


def save_transcript(responses: list[Response], session_dir: Path) -> Path:
    """Write the conversation to transcript.md."""
    lines = [f"# Conversation — {session_dir.name}", ""]
    for r in responses:
        speaker = "You" if r.author == "user" else "Tutor"
        ts = r.timestamp.strftime("%H:%M:%S")
        lines.append(f"**[{ts}] {speaker} ({r.language}):** {r.text}")
        lines.append("")
    path = session_dir / "transcript.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def format_transcript_for_lesson(responses: list[Response]) -> str:
    """Flatten the conversation into plain text for the lesson prompt."""
    lines = []
    for r in responses:
        speaker = "Student" if r.author == "user" else "Tutor"
        lines.append(f"{speaker} ({r.language}): {r.text}")
    return "\n".join(lines)


def _goal_context(goal: str | None) -> str:
    return GOAL_CONTEXT_TEMPLATE.format(goal=goal) if goal else ""


def _lang_names(target: str, native: str) -> dict:
    return {
        "target_name": languages.name(target),
        "native_name": languages.name(native),
    }


def build_system_prompt(daily: dict | None, target: str, native: str,
                        goal: str | None = None) -> str:
    """Build the LLM system prompt: story-framed if a daily story was
    prepared, otherwise the base conversational prompt.

    `target`/`native` are the practising user's language pair, and decide which
    language the tutor teaches in and which one it treats as already known.
    `goal` is the student's active focus goal, if they have one; it is optional
    so callers without a profile in hand keep working unchanged.
    """
    names = _lang_names(target, native)
    if not daily:
        return BASE_SYSTEM_PROMPT_TEMPLATE.format(**names) + _goal_context(goal)
    return STORY_SYSTEM_PROMPT_TEMPLATE.format(
        article_title=daily["article_title"],
        article_extract=daily["article_extract"][:CONTEXT_EXTRACT_CHARS],
        story=daily["story"],
        **names,
    ) + _goal_context(goal)


def build_challenge_system_prompt(lesson: dict, review: dict | None,
                                  target: str, native: str,
                                  goal: str | None = None) -> str:
    """System prompt for the conversation that follows a translation challenge.

    The tutor gets the story and the review findings, so "why is it *le* there"
    can be answered from the text the student just worked through instead of
    from a generic explanation.
    """
    review_context = ""
    if review:
        findings = "\n".join(
            f"- Sentence {f.get('sentence', '?')} ({f.get('verdict', '')}): "
            f"{f.get('topic', '')} — {f.get('explanation', '')}"
            for f in review.get("findings", [])
        )
        review_context = REVIEW_CONTEXT_TEMPLATE.format(
            summary=review.get("summary", ""), findings=findings)
    return CHALLENGE_SYSTEM_PROMPT_TEMPLATE.format(
        focus=lesson.get("focus", ""),
        story_title=lesson.get("title", ""),
        story=lesson.get("story", ""),
        review_context=review_context,
        **_lang_names(target, native),
    ) + _goal_context(goal)


def daily_story_from_setup_state(setup_state: dict) -> dict | None:
    """Flatten a session_setup_graph result into the {"article_title",
    "article_extract", "story_title", "story"} shape used by the system
    prompt and narration, or None if setup failed."""
    if setup_state.get("setup_failed"):
        return None
    story = setup_state["story"]
    return {
        "article_title": setup_state["article"]["title"],
        "article_extract": setup_state["article_extract"],
        "story_title": story["title"],
        "story": story["story"],
    }
