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

### Linux / WSL

```bash
# Install Python dependencies into project venv
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# Download Piper voice models
cd piper && bash download_voices.sh && cd ..
```

### Windows (native)

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

Each session run by `main.py` follows a five-phase arc:

1. **Prepare** — `curriculum.py` loads the persistent student profile (`recordings/student_profile.json`), fetches English Wikipedia's daily featured-content feed, has the LLM pick the most story-friendly candidate (avoiding recently covered topics and disturbing subject matter), fetches a fuller plaintext extract, and generates a graded ~150-200-word semi-fictional Spanish story from it (weaving in vocabulary the student needs to practice). Saved to the session folder as `article.md`/`story.md`. On any failure (Wikipedia unreachable, Ollama down, bad JSON) this degrades to a plain conversation with a printed warning — it never crashes.
2. **Narrate** — the story is read aloud once via `piper/tts.py:synthesize()` (Spanish voice) to `story_es.wav`, before the recording loop starts.
3. **Converse** — the existing push-to-talk loop: `audio_recorder/record.py:record_once()` does push-to-talk via raw terminal keypress (`e`=English, `s`=Spanish, SPACE=stop, `q`=quit) and returns `(language, wav_path)`; Whisper (`whisper/`, installed as a local editable package, loaded once at startup) transcribes; `query_llm()` in `main.py` calls a local Ollama instance (`llama3.1:8b` via HTTP at `localhost:11434`) with a per-turn language reminder injected so the tutor always replies in whichever language (`en`/`es`) the student just used; `synthesize()` speaks the reply. Each turn is saved as a uniquely timestamped WAV in the session folder and tracked in a `Response` dataclass list.
4. **Analyze** — at session end, `curriculum.analyze_weaknesses()` asks the LLM (JSON-constrained output) to identify concrete grammar/vocabulary weaknesses from the transcript, saved as `analysis.json`.
5. **Homework + persist** — `curriculum.generate_homework()` turns the analysis into a targeted `homework.md`; `curriculum.merge_analysis_into_profile()` updates the persistent profile (recurring weaknesses, vocab to practice, covered articles) so future sessions avoid repeat topics and reinforce chronic mistakes.

`curriculum.py` owns all Wikipedia/Ollama-content logic (fetching, prompts, JSON schemas, profile persistence); `main.py` stays the audio-session orchestrator.

## Key constraints

- Only two languages are supported: `"en"` and `"es"`. The language selection happens at record time and flows through the entire pipeline; the tutor mirrors it per turn (fixed from an earlier bug where it always replied in Spanish).
- Piper voices must be present in `piper/voices/` before `tts.py` can be imported — it loads them at module level.
- `audio_recorder/` has its own `venv` and `requirements.txt` (with `pynput`) that is separate from the root venv; the root `requirements.txt` uses `pyaudio` instead.
- `recordings/student_profile.json` is the one piece of cross-session state; it's gitignored (personal learning data) along with the rest of `recordings/`.
