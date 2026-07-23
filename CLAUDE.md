# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
# Start Ollama (required, in a separate terminal)
ollama serve

# Browser UI (recommended) — open http://127.0.0.1:8000 once it's up
# --reload restarts the server on any *.py change so you never hit stale
# backend code (uvicorn loads modules once at startup otherwise). It only
# watches Python files, so the WAV/JSON/MD artifacts sessions write into
# recordings/ don't trigger restarts.
uv run python -m uvicorn web.server:app --host 127.0.0.1 --port 8000 --reload

# UI-design demo mode — same frontend, but no mic/Ollama/Whisper/Kokoro:
# web/demo_manuscript.json's "session" key points at a recorded session
# folder, and each language button press replays its next real exchange
# (transcript text + the original WAV audio); the story bubble and homework
# come from the same folder, and the session wraps up automatically once the
# recorded turns run out. Without a "session" folder it falls back to the
# manuscript's inline turns with placeholder tones. Starts instantly; the
# manuscript is re-read on every connection, so edit and refresh the browser.
uv run python -m uvicorn web.demo:app --host 127.0.0.1 --port 8000 --reload

# Terminal UI (still supported)
python main.py
```

Both entry points auto-inject the `.venv` site-packages at startup, so no manual activation is needed.

## Setup (first time)

Windows only — this project targets native Windows exclusively (no Linux/WSL support).

```powershell
# Install Python dependencies into project venv
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Kokoro TTS model weights and per-language phonemizer data download automatically from Hugging Face on first use (cached under the user's Hugging Face cache dir) — no manual voice download step needed.

On Windows, `pyaudio` may need a pre-built wheel. If `pip install pyaudio` fails, install via:

```powershell
pip install pipwin
pipwin install pyaudio
```

## Architecture

A session follows a five-phase arc, shared by both the browser UI (`web/server.py` + `web/session.py`) and the terminal UI (`main.py`):

1. **Prepare** — `session_setup_graph` (a LangGraph workflow in `session_graphs.py`) loads the persistent student profile (`recordings/student_profile.json`), fetches English Wikipedia's "on this day" events feed for today's calendar date (`curriculum.fetch_onthisday_candidates()`, biased toward events before 1950 for richer historical material, falling back to the full day's pool if too few pre-1950 candidates exist), has the LLM pick the most story-friendly candidate (avoiding recently covered topics and disturbing subject matter), fetches a fuller plaintext extract, and generates a graded ~150-200-word semi-fictional Spanish story from it (weaving in vocabulary the student needs to practice). Saved to the session folder as `article.md`/`story.md`. Any failure (Wikipedia unreachable, Ollama down, bad JSON) sets `setup_failed` and the graph routes straight to `END`, so the caller degrades to a plain conversation instead of crashing.
2. **Narrate** — the story is read aloud once via `kokoro/tts.py:synthesize()` (Spanish voice) to `story_es.wav`, before the conversation loop starts. The terminal UI plays it locally (`play=True`); the browser UI shows the story as a normal assistant chat bubble and auto-plays that bubble's own `<audio>` element, which fetches `story_es.wav` from the session route.
3. **Converse** — per-turn: the student's speech is transcribed by Whisper (`whisper/`, installed as a local editable package, loaded once at startup) via `session_core.transcribe_audio()`; `session_core.query_llm()` calls a local Ollama instance (`llama3.1:8b` via HTTP at `localhost:11434`) with a per-turn language reminder injected so the tutor always replies in whichever language (`en`/`es`) the student just used; `synthesize()` speaks the reply. Each turn is saved as a uniquely timestamped WAV in the session folder and tracked in a `Response` dataclass list (`session_core.py`). The terminal UI drives this with `audio_recorder/record.py:record_once()` (raw-terminal push-to-talk: `e`=English, `s`=Spanish, SPACE=stop, `q`=quit) and local playback. The browser UI (`web/session.py:SessionOrchestrator`) drives the same logic over a WebSocket instead: the client captures the mic via the Web Audio API (`web/static/js/audio-capture.js`, an `AudioWorklet` encoding raw PCM into a WAV client-side — no ffmpeg/WebM decoding needed) and plays every sound through the chat bubble's own `<audio controls>` element — a `tts_audio` message is just the auto-play cue (no audio bytes cross the WebSocket), so each message is a single always-controllable source, and starting one bubble pauses the rest (`web/static/js/audio-playback.js` only routes bubble players to the chosen output device) — with independent input/output device pickers. Routing audio entirely through the browser lets the user pick a non-Bluetooth microphone while keeping a Bluetooth headset as output — since nothing then opens a mic stream against the headset, the OS never renegotiates it from the high-quality A2DP profile down to bidirectional HFP/HSP.
4. **Analyze** — `session_analysis_graph` (also in `session_graphs.py`) asks the LLM (JSON-constrained output via `curriculum.analyze_weaknesses()`) to identify concrete grammar/vocabulary weaknesses from the transcript, saved as `analysis.json`.
5. **Homework + persist** — the same graph turns the analysis into a targeted `homework.md` (`curriculum.generate_homework()`) and updates the persistent profile (recurring weaknesses, vocab to practice, covered articles) via `curriculum.merge_analysis_into_profile()`, so future sessions avoid repeat topics and reinforce chronic mistakes.

`curriculum.py` owns all Wikipedia/Ollama-content logic (fetching, prompts, JSON schemas, profile persistence). `session_graphs.py` sequences the Prepare and Analyze phases as LangGraph workflows (deterministic, mostly linear pipelines with a "skip to END on failure" pattern) — the real-time conversation loop is not a graph, since LangGraph adds nothing for interactive audio. `session_core.py` holds the logic shared by both UIs: transcription, LLM turn-taking, transcript persistence, and system-prompt construction. `main.py` is a thin terminal orchestrator; `web/server.py`/`web/session.py` is the equivalent browser orchestrator, driven by WebSocket messages instead of a blocking loop.

## Key constraints

- Only two languages are supported: `"en"` and `"es"`. The language selection happens at record time and flows through the entire pipeline; the tutor mirrors it per turn (fixed from an earlier bug where it always replied in Spanish).
- `kokoro/tts.py` loads a `KPipeline` per language at module level, which downloads Kokoro-82M weights from Hugging Face on first run if they aren't already cached; a cold first import can take a while on a slow connection.
- `audio_recorder/` has its own `venv` and `requirements.txt` that is separate from the root venv; the root `requirements.txt` uses `pyaudio` instead. It's only used by the terminal UI — the browser UI captures audio client-side.
- `recordings/student_profile.json` is the one piece of cross-session state; it's gitignored (personal learning data) along with the rest of `recordings/`.
- The browser UI's output-device picker relies on `HTMLMediaElement.setSinkId()`, which is Chromium-only as of writing (Chrome/Edge); other browsers fall back to the system default output device.
