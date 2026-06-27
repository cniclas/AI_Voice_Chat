# Final Plan: Spanish Teacher Assistant

Transform `main.py` from a generic bilingual voice chat into a personal Spanish
teacher. After a conversation ends, the transcript is analyzed by the LLM, which
produces a lesson targeting the student's mistakes. Both the transcript and the
lesson are saved as Markdown files inside a per-conversation, date-named folder.

We only touch `main.py`. The open-source libs (`whisper`, `record`, `tts`) are
used through their existing APIs and are not modified.

## Library APIs we rely on (unchanged)

- `whisper.load_model("base")` → model; `model.transcribe(path, language=)["text"]`
- `record_once(output_path) -> (language, path) | None` — push-to-talk recorder
- `synthesize(text, lang, output_file=, play=) ` — Piper TTS, "en"/"es"
- Ollama HTTP chat API at `localhost:11434/api/chat`

## On-disk layout

One folder per run, created at startup:

```
recordings/
  2026-06-27_143022/
    user_es_143055_001.wav
    assistant_es_143058_002.wav
    ...
    transcript.md
    lesson.md
```

## Changes to main.py

1. **`RECORDINGS_ROOT`** replaces the flat `AUDIO_STORAGE_DIR`; a fresh session
   folder is created per run.
2. **`Response` dataclass** gains a `timestamp: datetime` field so the transcript
   can show when each turn happened.
3. **System prompt** becomes a patient Spanish-tutor persona: always replies in
   Spanish, keeps the conversation flowing, does NOT correct mid-conversation
   (corrections belong in the lesson).
4. **`chat_completion(messages)`** — low-level Ollama call extracted from
   `query_llm`. Used by both the conversation loop and the one-shot lesson call,
   so lesson generation never pollutes the conversation history.
5. **`query_llm(user_text, history)`** — unchanged behavior, now built on
   `chat_completion`.
6. **`create_session_dir()`** — makes `recordings/YYYY-MM-DD_HHMMSS/`.
7. **`generate_audio_filename(session_dir, author, language)`** — writes audio
   into the session folder instead of the flat dir.
8. **`save_transcript(responses, session_dir)`** — writes `transcript.md`.
9. **`generate_lesson(responses)`** — sends the full transcript to the LLM with a
   lesson-builder prompt (summary, mistakes + corrections, useful vocabulary,
   3–5 exercises). Explanations in English, Spanish examples kept in Spanish.
10. **`save_lesson(lesson, session_dir)`** — writes `lesson.md`.
11. **`main()` teardown** — after the loop: if nothing was recorded, remove the
    empty folder and exit; otherwise save the transcript, generate the lesson,
    and save it. Ollama errors during lesson generation are caught so the
    transcript is never lost.

## Unchanged

`record_once`, `synthesize`, `transcribe_audio`, and the core
record → transcribe → LLM → speak loop keep their existing logic.
