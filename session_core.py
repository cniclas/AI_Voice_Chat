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
import requests

import backends
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


BASE_SYSTEM_PROMPT = (
    "Eres un profesor de español amable y paciente conversando con un estudiante. "
    "El estudiante puede hablar en inglés o en español; responde SIEMPRE en el "
    "mismo idioma que usó el estudiante en su último mensaje (se te indicará "
    "antes de cada mensaje). Habla de forma natural y conversacional, con frases "
    "cortas porque tus respuestas se leerán en voz alta. Mantén la conversación "
    "fluida y haz preguntas para que el estudiante siga hablando. No corrijas los "
    "errores en medio de la conversación; las correcciones se harán al final."
)

STORY_SYSTEM_PROMPT_TEMPLATE = BASE_SYSTEM_PROMPT + (
    "\n\nHoy la conversación gira en torno a un cuento que el estudiante acaba "
    "de escuchar, basado en un artículo de Wikipedia. Haz preguntas sobre el "
    "cuento, su tema y las opiniones del estudiante. Usa este contexto:\n\n"
    "ARTÍCULO ({article_title}):\n{article_extract}\n\n"
    "CUENTO:\n{story}"
)

CONTEXT_EXTRACT_CHARS = 1200  # article extract length embedded in the system prompt

LANG_REMINDER = {
    "en": "The student's last message is in English. Reply in English.",
    "es": "El último mensaje del estudiante está en español. Responde en español.",
}


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
    """Transcribe a WAV via the configured STT backend. `model` is the loaded
    local Whisper model, or None when the remote backend is configured."""
    cfg = backends.get("stt")
    if cfg["backend"] == "remote":
        return _transcribe_remote(audio_path, language, cfg)
    audio = load_audio_16k(audio_path)
    result = model.transcribe(audio, language=language)
    return result["text"].strip()


def _transcribe_remote(audio_path: str, language: str, cfg: dict) -> str:
    """POST the WAV to an ASR endpoint that returns {"text": ...} — the shape
    used by HuggingFace automatic-speech-recognition endpoints.

    Note: most hosted Whisper endpoints auto-detect the language and ignore
    the en/es choice made at record time; the local backend honors it. The
    language is passed as a query parameter for custom servers that do.
    """
    headers = {"Content-Type": "audio/wav"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    with open(audio_path, "rb") as f:
        data = f.read()
    response = requests.post(cfg["url"], data=data, headers=headers,
                             params={"language": language}, timeout=120)
    response.raise_for_status()
    return (response.json().get("text") or "").strip()


def create_session_dir() -> Path:
    """Create a new date/time-named folder for this conversation."""
    name = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    session_dir = Path(RECORDINGS_ROOT) / name
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
    student just spoke, instead of always answering in Spanish.
    """
    history.append({"role": "user", "content": user_text})
    messages = history[:-1] + [
        {"role": "system", "content": LANG_REMINDER[language]},
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


def build_system_prompt(daily: dict | None) -> str:
    """Build the LLM system prompt: story-framed if a daily story was
    prepared, otherwise the base conversational prompt."""
    if not daily:
        return BASE_SYSTEM_PROMPT
    return STORY_SYSTEM_PROMPT_TEMPLATE.format(
        article_title=daily["article_title"],
        article_extract=daily["article_extract"][:CONTEXT_EXTRACT_CHARS],
        story=daily["story"],
    )


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
