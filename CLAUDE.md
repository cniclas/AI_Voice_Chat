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
# (transcript text + the original WAV audio); the story bubble, lesson,
# review and homework come from the same folder, and the session wraps up
# automatically once the recorded turns run out. In the translation challenge
# the first press after the story plays the manuscript's "translation" block
# (spoken English, then the marked review). Without a "session" folder it
# falls back to the manuscript's inline turns with placeholder tones. Starts
# instantly; the manuscript is re-read on every connection, so edit and
# refresh the browser.
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

`requirements.txt` installs Whisper with `-e ./whisper`, and that directory is a trimmed copy of openai/whisper **committed to this repo** (it used to be gitignored, which meant a fresh clone could not install at all). Nothing to clone by hand. See `whisper/README.md` for the upstream commit it was taken from, what was removed, and how to update it.

On Windows, `pyaudio` may need a pre-built wheel. If `pip install pyaudio` fails, install via:

```powershell
pip install pipwin
pipwin install pyaudio
```

## Architecture

A session follows a five-phase arc, shared by both the browser UI (`web/server.py` + `web/session.py`) and the terminal UI (`main.py`):

1. **Prepare** — `session_setup_graph` (a LangGraph workflow in `session_graphs.py`) loads the persistent student profile (`recordings/student_profile.json`), fetches English Wikipedia's "on this day" events feed for today's calendar date (`curriculum.fetch_onthisday_candidates()`, biased toward events before 1950 for richer historical material, falling back to the full day's pool if too few pre-1950 candidates exist), has the LLM pick the most story-friendly candidate (avoiding recently covered topics and disturbing subject matter), fetches a fuller plaintext extract, and generates a graded ~150-200-word semi-fictional Spanish story from it (weaving in vocabulary the student needs to practice). Saved to the session folder as `article.md`/`story.md`. Any failure (Wikipedia unreachable, Ollama down, bad JSON) sets `setup_failed` and the graph routes straight to `END`, so the caller degrades to a plain conversation instead of crashing.

   The graph's `mode` picks the content branch. `"story"` (the terminal UI) generates the story above. `"challenge"` (the browser's translation challenge, below) instead builds a full graded lesson — story plus the sentence-by-sentence literal/natural translation — and additionally writes `lesson.md`. Both branches leave the prose in `story`, so narration, system prompts and homework downstream don't care which ran.
2. **Narrate** — the story is read aloud once via `kokoro/tts.py:synthesize()` (Spanish voice) to `story_es.wav`, before the conversation loop starts. The terminal UI plays it locally (`play=True`); the browser UI shows the story as a normal assistant chat bubble and auto-plays that bubble's own `<audio>` element, which fetches `story_es.wav` from the session route.
3. **Converse** — per-turn: the student's speech is transcribed by Whisper (`whisper/`, installed as a local editable package, loaded once at startup) via `session_core.transcribe_audio()`; `session_core.query_llm()` calls a local Ollama instance (`llama3.1:8b` via HTTP at `localhost:11434`) with a per-turn language reminder injected so the tutor always replies in whichever language (`en`/`es`) the student just used; `synthesize()` speaks the reply. Each turn is saved as a uniquely timestamped WAV in the session folder and tracked in a `Response` dataclass list (`session_core.py`). The terminal UI drives this with `audio_recorder/record.py:record_once()` (raw-terminal push-to-talk: `e`=English, `s`=Spanish, SPACE=stop, `q`=quit) and local playback. The browser UI (`web/session.py:SessionOrchestrator`) drives the same logic over a WebSocket instead: the client captures the mic via the Web Audio API (`web/static/js/audio-capture.js`, an `AudioWorklet` encoding raw PCM into a WAV client-side — no ffmpeg/WebM decoding needed) and plays every sound through the chat bubble's own `<audio controls>` element — a `tts_audio` message is just the auto-play cue (no audio bytes cross the WebSocket), so each message is a single always-controllable source, and starting one bubble pauses the rest (`web/static/js/audio-playback.js` only routes bubble players to the chosen output device) — with independent input/output device pickers. Routing audio entirely through the browser lets the user pick a non-Bluetooth microphone while keeping a Bluetooth headset as output — since nothing then opens a mic stream against the headset, the OS never renegotiates it from the high-quality A2DP profile down to bidirectional HFP/HSP.
4. **Analyze** — `session_analysis_graph` (also in `session_graphs.py`) asks the LLM (JSON-constrained output via `curriculum.analyze_weaknesses()`) to identify concrete grammar/vocabulary weaknesses from the transcript, saved as `analysis.json`. In a translation challenge the review's findings are folded into the same analysis first (`translation_challenge.merge_review_into_analysis()`), so what the reading exposed and what the speaking exposed are counted once, together.
5. **Homework + persist** — the same graph turns the analysis into a targeted `homework.md` (`curriculum.generate_homework()`), updates the persistent profile (recurring weaknesses, vocab to practice, covered articles) via `curriculum.merge_analysis_into_profile()`, and appends what the session actually *practiced* — mode, grammar focus, level, whether a translation was marked, how many spoken turns — via `curriculum.record_practice()`. Weaknesses say what the student gets wrong; `practice_log` and `focuses_practiced` say what they have worked on, which is what makes it visible that the subjunctive has been drilled four times and `por`/`para` never.

`curriculum.py` owns all Wikipedia/Ollama-content logic (fetching, prompts, JSON schemas, profile persistence). `session_graphs.py` sequences the Prepare and Analyze phases as LangGraph workflows (deterministic, mostly linear pipelines with a "skip to END on failure" pattern) — the real-time conversation loop is not a graph, since LangGraph adds nothing for interactive audio. `session_core.py` holds the logic shared by both UIs: transcription, LLM turn-taking, transcript persistence, and system-prompt construction. `main.py` is a thin terminal orchestrator; `web/server.py`/`web/session.py` is the equivalent browser orchestrator, driven by WebSocket messages instead of a blocking loop.

### Translation challenge (browser)

The browser's second entry point — replacing the old "today's Wikipedia story" button — runs the written-practice loop end to end inside a normal voice session, so the two Claude skills below have a local-model counterpart the app can drive on its own:

1. today's Wikipedia article is picked exactly as in Prepare, but becomes a **lesson** rather than a story to chat about: a graded story at the profile's level targeting a grammar focus, plus the sentence-by-sentence `Lit.`/`Nat.` translation that serves as the answer key, plus focus notes, vocabulary and speaking prompts (`translation_challenge.build_lesson()`, saved as `lesson.md`);
2. the Spanish story is narrated once, as in Narrate;
3. the student presses 🎙 English and **translates the story aloud**; that turn is transcribed and marked against the answer key (`translation_challenge.review_translation()`, saved as `translation_review.md`/`.json`, spoken back as a short summary and shown in full in the chat bubble);
4. push-to-talk then continues as usual, with the story and the review in the tutor's system prompt (`session_core.build_challenge_system_prompt()`) so "why is it *le* in sentence four" is answered from the text the student just worked through. Answering in Spanish during step 3 is read as "I'd rather just talk" and skips straight here;
5. Exit runs Analyze + Homework + persist, with the review's findings included.

`lesson.md` is deliberately not linked until the translation has been marked — it contains the answer key. The two artifacts are then offered in the chat (an `artifact` WebSocket message) and again on the completion screen.

`translation_challenge.py` owns the prompts, JSON schemas and markdown rendering for both halves; `skill_refs.py` feeds them by pulling the *exact* sections the skills tell a reader to consult out of `.claude/skills/**/references/` — the CEFR band for the level, the Spanish forms admitted at that level, the focus pattern being drilled, the glossing notation and its worked examples, the judging filters, the Spanish→English trap catalogue. The skill files stay the single source of truth: editing `references/languages/es.md` changes what the app generates with no code change, and a lesson written by Claude and one generated by the app are calibrated to the same scale. The work is split into small separately-validated Ollama calls (story, then one gloss per sentence, then notes) because llama3.1:8b reliably drops half the output when asked for all of it at once; the glossing pass is why building a challenge takes about a minute, and the progress callback threaded through the graph's run config exists so that minute doesn't look like a hang.

## Skills

Two Claude-run skills form a written-practice loop alongside the spoken session pipeline:
`language-lesson` produces a graded story, the student reads their English translation of it
aloud, and `translation-review` marks that translation. Both run on Claude rather than the
local Ollama model, and both read and write the same `recordings/student_profile.json`, so
weaknesses found by reading accumulate in the same tally as weaknesses found by speaking.

The browser's translation challenge runs that same loop unattended against Ollama —
`translation_challenge.py` is the port, `skill_refs.py` is what keeps it honest by feeding
the prompts out of these skills' own reference files. Use the skills when you want Claude's
judgment (a lesson in a new language, a careful review, a follow-up conversation about a
gloss); the app's version exists so a session can do it without Claude in the loop.

### language-lesson

`.claude/skills/language-lesson/` builds a standalone written lesson from any source text
(story, article, fact block): a graded story targeting a chosen grammar focus at a CEFR
level, plus a word-for-word literal English gloss alongside a natural translation, so the
learner can trace any single word. It runs on Claude rather than the local Ollama model and
is independent of the session pipeline above, but reads the same
`recordings/student_profile.json` for the default level and vocab to weave in, and writes to
a gitignored `lessons/` directory.

Its references split along what generalizes and what doesn't: `references/levels.md` holds
the language-neutral CEFR bands (text budget, clause complexity, the *functions* a reader
can handle), while `references/languages/<code>.md` maps those functions onto one language's
forms, focus patterns, and glossing quirks. Only `es.md` ships complete; the skill writes a
new language file the first time it's asked for another language, so a second lesson in that
language stays calibrated to the same scale. Note that lessons outside `en`/`es` are
text-only — `kokoro/tts.py` has no voice for them.

### translation-review

`.claude/skills/translation-review/` marks a spoken translation. The student reads a lesson's
Spanish aloud in English, Whisper transcribes it, and the skill compares that against the
lesson's `Nat.` line (the target) using the `Lit.` gloss to pinpoint which morpheme was lost.

Because the student is translating *into* their native language, their English is a readout
rather than the subject — every deviation is read as a question about the Spanish. The hard
part is separating comprehension errors from transcription noise: spoken input has no
spelling, so homophones, punctuation, and disfluency are never findings, and the working
discriminator is repetition (one deviation is a mishearing, the same one three times is a
rule the student is missing). Findings land in one of three verdicts — Missed (meaning
wrong), Blurred (a drilled distinction flattened), Check (English can't show it, so ask
instead of guessing). `references/judging.md` holds that language-neutral logic;
`references/languages/es.md` holds the Spanish→English trap catalogue.

`scripts/transcribe.py` wraps `session_core.transcribe_audio()` so transcripts match what a
live session would produce; `scripts/record_review.py` merges findings into the profile via
`curriculum.merge_analysis_into_profile()` rather than hand-editing the JSON, keeping the
`occurrences` tally consistent with the session pipeline's.

## Key constraints

- Only two languages are supported: `"en"` and `"es"`. The language selection happens at record time and flows through the entire pipeline; the tutor mirrors it per turn (fixed from an earlier bug where it always replied in Spanish).
- `kokoro/tts.py` loads a `KPipeline` per language at module level, which downloads Kokoro-82M weights from Hugging Face on first run if they aren't already cached; a cold first import can take a while on a slow connection.
- `audio_recorder/` has its own `venv` and `requirements.txt` that is separate from the root venv; the root `requirements.txt` uses `pyaudio` instead. It's only used by the terminal UI — the browser UI captures audio client-side.
- `recordings/student_profile.json` is the one piece of cross-session state; it's gitignored (personal learning data) along with the rest of `recordings/`.
- The browser UI's output-device picker relies on `HTMLMediaElement.setSinkId()`, which is Chromium-only as of writing (Chrome/Edge); other browsers fall back to the system default output device.
