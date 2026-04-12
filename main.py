import sys
import os
import glob
import site
import time
import shutil
from datetime import datetime
from pathlib import Path

# Inject the project venv's site-packages so the script works without activation
_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in glob.glob(os.path.join(_ROOT, ".venv", "lib", "python*", "site-packages")):
    site.addsitedir(_p)

import requests

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "whisper"))
sys.path.insert(0, os.path.join(_ROOT, "piper"))
sys.path.insert(0, os.path.join(_ROOT, "audio_recorder"))

import whisper
from record import record_once
from tts import synthesize
from dataclasses import dataclass
from typing import Optional

RECORDING_PATH = "/tmp/voice_chat_recording.wav"
AUDIO_STORAGE_DIR = os.path.join(_ROOT, "recordings")

# Create recordings directory if it doesn't exist
Path(AUDIO_STORAGE_DIR).mkdir(exist_ok=True)


@dataclass
class Response:
    """Represents a single exchange in the conversation."""
    author: str  # "user" or "assistant"
    language: str  # "en", "es", etc.
    text: str
    audio_sample: Optional[str] = None  # Path to audio file


OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_URL = "http://localhost:11434/api/chat"
SYSTEM_PROMPT = (
    "You are a helpful voice assistant. "
    "Keep responses concise and conversational, as they will be spoken aloud."
)


def transcribe_audio(audio_path: str, model, language: str = "en") -> str:
    result = model.transcribe(audio_path, language=language)
    return result["text"].strip()


def generate_unique_audio_filename(author: str, language: str) -> str:
    """Generate a unique filename for storing audio samples."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # Include milliseconds
    filename = f"{author}_{language}_{timestamp}.wav"
    return os.path.join(AUDIO_STORAGE_DIR, filename)


def query_llm(user_text: str, history: list) -> str:
    history.append({"role": "user", "content": user_text})
    response = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "messages": history, "stream": False},
        timeout=60,
    )
    response.raise_for_status()
    assistant_text = response.json()["message"]["content"]
    history.append({"role": "assistant", "content": assistant_text})
    return assistant_text


def main():
    print("Loading Whisper model...")
    whisper_model = whisper.load_model("base")
    print("Whisper ready.\n")

    responses: list[Response] = []  # Track all conversation exchanges
    llm_history = [{"role": "system", "content": SYSTEM_PROMPT}]  # For LLM API calls

    print("AI Voice Chat")
    print("Press 'e' for English or 's' for Spanish to start recording. Press SPACE to stop. Press 'q' to quit.\n")

    while True:
        result = record_once(RECORDING_PATH)
        if result is None:
            print("Goodbye!")
            break

        language, audio_path = result

        print("Transcribing...")
        user_text = transcribe_audio(audio_path, whisper_model, language=language)
        if not user_text:
            print("No speech detected, try again.\n")
            continue
        print(f"You: {user_text}")

        # Save user audio to a unique file
        unique_user_audio = generate_unique_audio_filename("user", language)
        shutil.copy(audio_path, unique_user_audio)

        # Store user response
        user_response = Response(
            author="user",
            language=language,
            text=user_text,
            audio_sample=unique_user_audio
        )
        responses.append(user_response)

        print("Thinking...")
        try:
            response_text = query_llm(user_text, llm_history)
        except requests.RequestException as e:
            print(f"Ollama error: {e}")
            print("Make sure Ollama is running: ollama serve\n")
            continue
        print(f"Assistant: {response_text}")

        # Generate unique filename for assistant audio
        unique_assistant_audio = generate_unique_audio_filename("assistant", language)

        # Store assistant response
        assistant_response = Response(
            author="assistant",
            language=language,
            text=response_text,
            audio_sample=unique_assistant_audio
        )
        responses.append(assistant_response)

        print("Speaking...")
        time.sleep(0.5)  # Buffer delay before playback
        synthesize(response_text, lang=language, output_file=unique_assistant_audio, play=True)
        print()


if __name__ == "__main__":
    main()
