import sys
import os
import glob
import site

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

RECORDING_PATH = "/tmp/voice_chat_recording.wav"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_URL = "http://localhost:11434/api/chat"
SYSTEM_PROMPT = (
    "You are a helpful voice assistant. "
    "Keep responses concise and conversational, as they will be spoken aloud."
)


def transcribe_audio(audio_path: str, model) -> str:
    result = model.transcribe(audio_path)
    return result["text"].strip()


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

    conversation = [{"role": "system", "content": SYSTEM_PROMPT}]

    print("AI Voice Chat")
    print("Press SPACE to start/stop recording. Press 'q' to quit.\n")

    while True:
        audio_path = record_once(RECORDING_PATH)
        if audio_path is None:
            print("Goodbye!")
            break

        print("Transcribing...")
        user_text = transcribe_audio(audio_path, whisper_model)
        if not user_text:
            print("No speech detected, try again.\n")
            continue
        print(f"You: {user_text}")

        print("Thinking...")
        try:
            response_text = query_llm(user_text, conversation)
        except requests.RequestException as e:
            print(f"Ollama error: {e}")
            print("Make sure Ollama is running: ollama serve\n")
            continue
        print(f"Assistant: {response_text}")

        print("Speaking...")
        synthesize(response_text, lang="en", play=True)
        print()


if __name__ == "__main__":
    main()
