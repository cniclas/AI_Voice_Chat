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
_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in glob.glob(os.path.join(_ROOT, ".venv", "Lib", "site-packages")):
    site.addsitedir(_p)

import requests

sys.path.insert(0, os.path.join(_ROOT, "whisper"))
sys.path.insert(0, os.path.join(_ROOT, "kokoro"))
sys.path.insert(0, os.path.join(_ROOT, "audio_recorder"))

import whisper
from record import record_once
from tts import synthesize

from session_graphs import session_setup_graph, session_analysis_graph
from session_core import (
    Response,
    create_session_dir,
    generate_audio_filename,
    query_llm,
    save_transcript,
    format_transcript_for_lesson,
    transcribe_audio,
    build_system_prompt,
    daily_story_from_setup_state,
)

RECORDING_PATH = os.path.join(tempfile.gettempdir(), "voice_chat_recording.wav")


def main():
    session_dir = create_session_dir()
    responses: list[Response] = []  # Track all conversation exchanges

    print("AI Spanish Teacher")
    print(f"Session folder: {session_dir}")

    print("Fetching today's Wikipedia story...")
    setup_state = session_setup_graph.invoke({"session_dir": str(session_dir)})
    profile = setup_state["profile"]
    if setup_state.get("setup_failed"):
        print(f"Could not prepare today's story ({setup_state['setup_failed']}); "
              "starting a plain conversation instead.")
    daily = daily_story_from_setup_state(setup_state)

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
    else:
        print("(No Wikipedia story available today — starting a plain conversation.)")

    llm_history = [{"role": "system", "content": build_system_prompt(daily)}]

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
