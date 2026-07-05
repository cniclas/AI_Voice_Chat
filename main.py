import sys
import os
import glob
import site
import time
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

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


OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_URL = "http://localhost:11434/api/chat"

SYSTEM_PROMPT = (
    "Eres un profesor de español amable y paciente conversando con un estudiante. "
    "Responde siempre en español, de forma natural y conversacional, con frases "
    "cortas porque tus respuestas se leerán en voz alta. Mantén la conversación "
    "fluida y haz preguntas para que el estudiante siga hablando. No corrijas los "
    "errores en medio de la conversación; las correcciones se harán en una lección "
    "al final."
)

LESSON_SYSTEM_PROMPT = (
    "You are an expert Spanish teacher. You will be given the transcript of a "
    "spoken conversation between a student (the user) and a Spanish tutor (the "
    "assistant). Analyze the student's Spanish and write a focused lesson in "
    "Markdown."
)


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


def chat_completion(messages: list) -> str:
    """Low-level Ollama chat call. Returns the assistant's text."""
    response = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "messages": messages, "stream": False},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def query_llm(user_text: str, history: list) -> str:
    """Append the user turn, get a reply, and record it in the history."""
    history.append({"role": "user", "content": user_text})
    assistant_text = chat_completion(history)
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


def generate_lesson(responses: list[Response]) -> str:
    """Ask the LLM to build a lesson from the conversation transcript."""
    transcript = format_transcript_for_lesson(responses)
    user_prompt = (
        "Here is the conversation transcript:\n\n"
        f"{transcript}\n\n"
        "Write a Spanish lesson in Markdown based on the student's turns. Include:\n"
        "1. A short summary of what the conversation was about.\n"
        "2. The main grammar and vocabulary mistakes the student made, each with "
        "the correction and a brief explanation in English.\n"
        "3. Useful vocabulary or expressions the student could have used.\n"
        "4. 3-5 practice exercises targeting the student's weaknesses.\n\n"
        "Write explanations in English, but keep all Spanish examples in Spanish."
    )
    messages = [
        {"role": "system", "content": LESSON_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return chat_completion(messages)


def save_lesson(lesson: str, session_dir: Path) -> Path:
    """Write the generated lesson to lesson.md."""
    path = session_dir / "lesson.md"
    path.write_text(f"# Spanish Lesson — {session_dir.name}\n\n{lesson}\n", encoding="utf-8")
    return path


def main():
    print("Loading Whisper model...")
    whisper_model = whisper.load_model("base")
    print("Whisper ready.\n")

    session_dir = create_session_dir()
    responses: list[Response] = []  # Track all conversation exchanges
    llm_history = [{"role": "system", "content": SYSTEM_PROMPT}]  # For LLM API calls

    print("AI Spanish Teacher")
    print(f"Session folder: {session_dir}")
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
            response_text = query_llm(user_text, llm_history)
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

    print("Analyzing the conversation and building your lesson (this may take a moment)...")
    try:
        lesson = generate_lesson(responses)
        lesson_path = save_lesson(lesson, session_dir)
        print(f"Lesson saved to {lesson_path}")
    except requests.RequestException as e:
        print(f"Could not generate lesson (Ollama error): {e}")

    print("¡Hasta luego!")


if __name__ == "__main__":
    main()
