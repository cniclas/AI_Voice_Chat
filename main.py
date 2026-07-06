import sys
import os
import glob
import site
import time
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

# Windows terminals often default to a legacy codepage (e.g. cp1252) that
# can't encode arbitrary Wikipedia-article/story text (accents, "đ", etc.).
# Force UTF-8 on stdout/stderr so printing never crashes the session.
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Inject the project venv's site-packages so the script works without activation.
# Layout differs by platform: Linux/macOS use lib/pythonX.Y/site-packages,
# native Windows uses Lib/site-packages.
_ROOT = os.path.dirname(os.path.abspath(__file__))
_SITE_PACKAGE_GLOBS = [
    os.path.join(_ROOT, ".venv", "lib", "python*", "site-packages"),  # Linux/macOS
    os.path.join(_ROOT, ".venv", "Lib", "site-packages"),             # Windows
]
for _pattern in _SITE_PACKAGE_GLOBS:
    for _p in glob.glob(_pattern):
        site.addsitedir(_p)

import wave
import requests
import numpy as np

sys.path.insert(0, os.path.join(_ROOT, "whisper"))
sys.path.insert(0, os.path.join(_ROOT, "piper"))
sys.path.insert(0, os.path.join(_ROOT, "audio_recorder"))

import whisper
from record import record_once
from tts import synthesize
from dataclasses import dataclass
from typing import Optional

from curriculum import chat_completion
from session_graphs import session_setup_graph, session_analysis_graph

RECORDING_PATH = os.path.join(tempfile.gettempdir(), "voice_chat_recording.wav")
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
    audio = load_audio_16k(audio_path)
    result = model.transcribe(audio, language=language)
    return result["text"].strip()


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


def main():
    session_dir = create_session_dir()
    responses: list[Response] = []  # Track all conversation exchanges

    print("AI Spanish Teacher")
    print(f"Session folder: {session_dir}")

    print("Fetching today's Wikipedia story...")
    setup_state = session_setup_graph.invoke({"session_dir": str(session_dir)})
    profile = setup_state["profile"]
    daily = None
    if setup_state.get("setup_failed"):
        print(f"Could not prepare today's story ({setup_state['setup_failed']}); "
              "starting a plain conversation instead.")
    else:
        story = setup_state["story"]
        daily = {
            "article_title": setup_state["article"]["title"],
            "article_extract": setup_state["article_extract"],
            "story_title": story["title"],
            "story": story["story"],
        }

    print("Loading Whisper model...")
    whisper_model = whisper.load_model("large-v3")
    print("Whisper ready.\n")

    if daily:
        print(f"\nCuento de hoy: {daily['story_title']}\n\n{daily['story']}\n")
        print("Reading today's story aloud...")
        time.sleep(0.5)
        try:
            synthesize(
                f"{daily['story_title']}. {daily['story']}",
                lang="es",
                output_file=str(session_dir / "story_es.wav"),
                play=True,
            )
        except Exception as e:
            print(f"Warning: could not narrate the story ({e}); continuing anyway.")
        time.sleep(0.5)

        system_prompt = STORY_SYSTEM_PROMPT_TEMPLATE.format(
            article_title=daily["article_title"],
            article_extract=daily["article_extract"][:CONTEXT_EXTRACT_CHARS],
            story=daily["story"],
        )
    else:
        print("(No Wikipedia story available today — starting a plain conversation.)")
        system_prompt = BASE_SYSTEM_PROMPT

    llm_history = [{"role": "system", "content": system_prompt}]  # For LLM API calls

    print("Press 'e' for English or 's' for Spanish to start recording. Press SPACE to stop. Press 'q' to quit.\n")

    while True:
        result = record_once(RECORDING_PATH)
        if result is None:
            break

        language, audio_path = result

        print("Transcribing...")
        user_text = transcribe_audio(audio_path, whisper_model, language=language)
        if not user_text:
            print("No speech detected, try again.\n")
            continue
        print(f"You: {user_text}")

        # Save user audio into the session folder
        unique_user_audio = generate_audio_filename(session_dir, "user", language)
        shutil.copy(audio_path, unique_user_audio)

        responses.append(Response(
            author="user",
            language=language,
            text=user_text,
            timestamp=datetime.now(),
            audio_sample=unique_user_audio,
        ))

        print("Thinking...")
        try:
            response_text = query_llm(user_text, language, llm_history)
        except requests.RequestException as e:
            print(f"Ollama error: {e}")
            print("Make sure Ollama is running: ollama serve\n")
            continue
        print(f"Tutor: {response_text}")

        unique_assistant_audio = generate_audio_filename(session_dir, "assistant", language)

        responses.append(Response(
            author="assistant",
            language=language,
            text=response_text,
            timestamp=datetime.now(),
            audio_sample=unique_assistant_audio,
        ))

        print("Speaking...")
        time.sleep(0.5)  # Buffer delay before playback
        synthesize(response_text, lang=language, output_file=unique_assistant_audio, play=True)
        time.sleep(0.5)
        print()

    # Conversation finished — produce transcript and lesson
    if not responses:
        print("No conversation recorded. ¡Hasta luego!")
        try:
            session_dir.rmdir()  # Remove the empty session folder
        except OSError:
            pass
        return

    transcript_path = save_transcript(responses, session_dir)
    print(f"\nTranscript saved to {transcript_path}")

    print("Analyzing your Spanish (this may take a moment)...")
    transcript_text = format_transcript_for_lesson(responses)
    session_analysis_graph.invoke({
        "session_dir": str(session_dir),
        "profile": profile,
        "transcript_text": transcript_text,
        "story": setup_state.get("story"),
    })

    print("¡Hasta luego!")


if __name__ == "__main__":
    main()
