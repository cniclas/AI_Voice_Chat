# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
# Start Ollama (required, in a separate terminal)
ollama serve

# Run the voice chat
python main.py
```

`main.py` auto-injects the `.venv` site-packages at startup, so no manual activation is needed.

## Setup (first time)

```bash
# Install Python dependencies into project venv
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# Download Piper voice models
cd piper && bash download_voices.sh && cd ..
```

## Architecture

The pipeline runs sequentially in a loop inside `main.py`:

1. **Record** — `audio_recorder/record.py:record_once()` does push-to-talk via raw terminal keypress (`e`=English, `s`=Spanish, SPACE=stop, `q`=quit). Returns `(language, wav_path)`.
2. **Transcribe** — Whisper (`whisper/`) is installed as a local editable package (`-e ./whisper` in `requirements.txt`). The base model is loaded once at startup.
3. **Generate** — a LangGraph graph (`conversation_graph.py`) handles each turn: an `analyze` node asks the LLM whether the transcribed utterance is coherent (learner mistakes count as coherent; only garbled/mis-transcribed speech does not), then routes to `respond` (normal tutor reply) or `clarify` (asks the student to repeat, capped at 2 consecutive times). All LLM calls go through `llm.py:chat_completion()`, which hits a local Ollama instance (`llama3.1:8b`) via HTTP at `localhost:11434`.
4. **Speak** — `piper/tts.py:synthesize()` loads both voice models (`en_US-lessac-medium`, `es_MX-claude-high`) once at import time and writes/plays WAV files via `sounddevice`.

Graph nodes never mutate the passed-in `messages` list; `main.py` reassigns `llm_history` from the returned state each turn. Lesson generation (`generate_lesson`) stays a one-shot `chat_completion` call outside the graph.

Each user and assistant turn is saved as a uniquely timestamped WAV in `recordings/` and tracked in a `Response` dataclass list.

## Key constraints

- Only two languages are supported: `"en"` and `"es"`. The language selection happens at record time and flows through the entire pipeline.
- Piper voices must be present in `piper/voices/` before `tts.py` can be imported — it loads them at module level.
- `audio_recorder/` has its own `venv` and `requirements.txt` (with `pynput`) that is separate from the root venv; the root `requirements.txt` uses `pyaudio` instead.
