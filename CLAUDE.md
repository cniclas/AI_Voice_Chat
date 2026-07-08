# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
# Start Ollama (required, in a separate terminal)
ollama serve

# Browser UI (recommended) — starts the server and opens the browser automatically
python run.py            # or double-click start.bat on Windows

# Browser UI without auto-opening a browser
uv run python -m uvicorn web.server:app --host 127.0.0.1 --port 8000

# Terminal UI (still supported, Wikipedia-story mode only)
python main.py
```

Both entry points auto-inject the `.venv` site-packages at startup, so no manual activation is needed.

## Setup (first time)

Windows only — this project targets native Windows exclusively (no Linux/WSL support).

```powershell
# Install Python dependencies into project venv
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Download Piper voice models
cd piper; .\download_voices.ps1; cd ..
```

On Windows, `pyaudio` may need a pre-built wheel. If `pip install pyaudio` fails, install via:

```powershell
pip install pipwin
pipwin install pyaudio
```

## Architecture

### Model backends (local vs remote)

The three model services are pluggable via `backends.py`, configured by an optional `backends.json` at the repo root (gitignored — it may hold API keys; see `backends.example.json`). No file → all-local defaults, identical to the original behavior. Any mix is allowed (e.g. Whisper local, LLM + Piper remote):

- **LLM** (`curriculum.chat_completion()`): `"ollama"` (local or remote Ollama via `base_url`) or `"openai"` — any OpenAI-compatible `/v1/chat/completions` endpoint (HuggingFace serverless router `https://router.huggingface.co/v1`, dedicated HF Inference Endpoints/TGI, vLLM, ...). JSON-schema constraints map to `response_format: json_schema`; servers that reject constrained decoding get one retry without it (the prompts already ask for JSON and `chat_completion_json` validates).
- **STT** (`session_core.transcribe_audio()`): `"local"` (the bundled Whisper package; only then is the model loaded at startup) or `"remote"` (POST WAV body → `{"text": ...}`, the HF automatic-speech-recognition shape). Caveat: hosted Whisper endpoints usually auto-detect language and ignore the per-turn en/es choice — a reason to keep STT local.
- **TTS** (`piper/tts.py:synthesize()`): `"local"` (bundled Piper voices, now loaded lazily on first use) or `"remote"` (POST `{"text", "lang"}` → WAV bytes). `remote/piper_server.py` is a self-contained FastAPI server implementing that contract for the remote host (Piper is CPU-fast; no GPU needed), with optional bearer-token auth via `PIPER_SERVER_TOKEN`.

### Conversation modes (browser UI)

The browser UI offers four mutually exclusive modes, picked on the start screen. One WebSocket connection = one session in one mode; the first client message (`{"type": "start", "mode": ...}`) selects which `web/session.py` orchestrator class runs (`MODE_SESSIONS` dispatch in `web/server.py`):

- **`story`** (`StorySession`) — the original Wikipedia-story arc described below, now triggered explicitly instead of automatically.
- **`homework`** (`HomeworkSession`) — a voice conversation steered by a defined lesson: the latest `recordings/*/homework.md` (or a lesson built deterministically from the student profile if none exists). The tutor opens the session itself and drives it exercise by exercise (`exercises.py`: `find_latest_homework()`, `build_homework_system_prompt()`, `homework_opening_turn()`).
- **`flashcards`** (`FlashcardSession`) — voice input only, no spoken LLM replies. An LLM-generated deck of English→Spanish cards (`exercises.generate_flashcards()`, seeded from the profile's practice vocab); the student says the Spanish word and an LLM judge grades it, accepting synonyms/accents/articles (`exercises.judge_flashcard()`), falling back to a normalized string match if Ollama is down.
- **`fill_blanks`** (`FillBlankSession`) — same card/judge shape, but with LLM-generated cloze sentences (`generate_fill_blanks()`/`judge_fill_blank()`).

Class hierarchy in `web/session.py`: `BaseSession` (WebSocket plumbing, user-turn capture/transcription, TTS streaming, discard-on-abort) → `ChatSessionBase` (free conversation loop + analysis/homework on finish; parents of `StorySession`/`HomeworkSession`) and `ExerciseSessionBase` (card → answer → judgement loop, `results.md` + profile vocab bump on completion; parents of `FlashcardSession`/`FillBlankSession`).

Quit semantics: the client can send `abort_session` at any time (the Quit button, or clicking another mode mid-session, which aborts and starts the new mode directly). An aborted — or disconnected — session persists **nothing**: the whole session folder is deleted. Only completion persists artifacts: chat modes on `end_session` (transcript + analysis + homework), exercise modes on finishing the deck. The terminal UI (`main.py`) still runs story mode only.

### The story-session arc

A story session follows a five-phase arc, shared by both the browser UI (`web/server.py` + `web/session.py:StorySession`) and the terminal UI (`main.py`):

1. **Prepare** — `session_setup_graph` (a LangGraph workflow in `session_graphs.py`) loads the persistent student profile (`recordings/student_profile.json`), fetches English Wikipedia's "on this day" events feed for today's calendar date (`curriculum.fetch_onthisday_candidates()`, biased toward events before 1950 for richer historical material, falling back to the full day's pool if too few pre-1950 candidates exist), has the LLM pick the most story-friendly candidate (avoiding recently covered topics and disturbing subject matter), fetches a fuller plaintext extract, and generates a graded ~150-200-word semi-fictional Spanish story from it (weaving in vocabulary the student needs to practice). Saved to the session folder as `article.md`/`story.md`. Any failure (Wikipedia unreachable, Ollama down, bad JSON) sets `setup_failed` and the graph routes straight to `END`, so the caller degrades to a plain conversation instead of crashing.
2. **Narrate** — the story is read aloud once via `piper/tts.py:synthesize()` (Spanish voice) to `story_es.wav`, before the conversation loop starts. The terminal UI plays it locally (`play=True`); the browser UI gets the WAV bytes back (`play=False`) and plays them client-side.
3. **Converse** — per-turn: the student's speech is transcribed by Whisper (`whisper/`, installed as a local editable package, loaded once at startup) via `session_core.transcribe_audio()`; `session_core.query_llm()` calls a local Ollama instance (`llama3.1:8b` via HTTP at `localhost:11434`) with a per-turn language reminder injected so the tutor always replies in whichever language (`en`/`es`) the student just used; `synthesize()` speaks the reply. Each turn is saved as a uniquely timestamped WAV in the session folder and tracked in a `Response` dataclass list (`session_core.py`). The terminal UI drives this with `audio_recorder/record.py:record_once()` (raw-terminal push-to-talk: `e`=English, `s`=Spanish, SPACE=stop, `q`=quit) and local playback. The browser UI (`web/session.py:StorySession`) drives the same logic over a WebSocket instead: the client captures the mic via the Web Audio API (`web/static/js/audio-capture.js`, an `AudioWorklet` encoding raw PCM into a WAV client-side — no ffmpeg/WebM decoding needed) and plays replies via an `<audio>` element (`web/static/js/audio-playback.js`), with independent input/output device pickers. Routing audio entirely through the browser lets the user pick a non-Bluetooth microphone while keeping a Bluetooth headset as output — since nothing then opens a mic stream against the headset, the OS never renegotiates it from the high-quality A2DP profile down to bidirectional HFP/HSP.
4. **Analyze** — `session_analysis_graph` (also in `session_graphs.py`) asks the LLM (JSON-constrained output via `curriculum.analyze_weaknesses()`) to identify concrete grammar/vocabulary weaknesses from the transcript, saved as `analysis.json`.
5. **Homework + persist** — the same graph turns the analysis into a targeted `homework.md` (`curriculum.generate_homework()`) and updates the persistent profile (recurring weaknesses, vocab to practice, covered articles) via `curriculum.merge_analysis_into_profile()`, so future sessions avoid repeat topics and reinforce chronic mistakes.

`curriculum.py` owns all Wikipedia/Ollama-content logic for the story arc (fetching, prompts, JSON schemas, profile persistence) plus the shared Ollama chat helpers. `exercises.py` owns the content and grading for the other modes (homework lesson loading/prompting, flashcard/fill-blank generation, the LLM judge, `results.md`). `session_graphs.py` sequences the Prepare and Analyze phases as LangGraph workflows (deterministic, mostly linear pipelines with a "skip to END on failure" pattern) — the real-time conversation loop is not a graph, since LangGraph adds nothing for interactive audio. `session_core.py` holds the logic shared by both UIs: transcription, LLM turn-taking, transcript persistence, and system-prompt construction. `main.py` is a thin terminal orchestrator; `web/server.py`/`web/session.py` is the equivalent browser orchestrator, driven by WebSocket messages instead of a blocking loop.

## Key constraints

- Only two languages are supported: `"en"` and `"es"`. The language selection happens at record time and flows through the entire pipeline; the tutor mirrors it per turn (fixed from an earlier bug where it always replied in Spanish).
- With the local TTS backend, Piper voices must be present in `piper/voices/` before the first `synthesize()` call — they are loaded lazily per language and cached. With the remote backend no local voices are needed.
- `audio_recorder/` has its own `venv` and `requirements.txt` that is separate from the root venv; the root `requirements.txt` uses `pyaudio` instead. It's only used by the terminal UI — the browser UI captures audio client-side.
- `recordings/student_profile.json` is the one piece of cross-session state; it's gitignored (personal learning data) along with the rest of `recordings/`.
- The browser UI's output-device picker relies on `HTMLMediaElement.setSinkId()`, which is Chromium-only as of writing (Chrome/Edge); other browsers fall back to the system default output device.
