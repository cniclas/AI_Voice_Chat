"""DemoSessionOrchestrator: a stand-in for SessionOrchestrator that speaks the
exact same WebSocket protocol but never touches Whisper, Ollama, Kokoro or
Wikipedia. Every button press advances a predefined script instead, so the
browser UI can be designed and iterated on with instant startup and zero
model downloads.

The script comes from web/demo_manuscript.json. If the manuscript names a
recorded session folder (its "session" key, e.g.
"recordings/2026-07-23_235034"), the demo replays that real session: story.md
and article.md provide the story panel, transcript.md provides the
conversation turns, homework.md provides the wrap-up artifact, lesson.md and
translation_review.md (when the session was a translation challenge) provide
the linked artifacts, and the session's WAV files provide the actual audio
(the student's real voice and Kokoro's real replies) — no synthesis needed. If
the folder is missing or unparsable, the manuscript's inline
"story"/"turns"/"translation"/"homework" keys are used instead, with generated
placeholder tones for audio.

The translation challenge is scripted the same way: the first button press
after the story plays the manuscript's "translation" block (the student's
spoken English, then the marked review) instead of a conversation turn, and
the remaining presses continue through "turns". The linked lesson.md is the
recorded session's when there is one, and otherwise just the story prose —
the demo exists to lay out the UI, not to fake an answer key.

The reverse challenge (``direction: "into_target"``) is scripted by the
manuscript's "translation_into_target" block instead: its "prompt" is the
native-language text narrated in place of the story, and its "student" line is
the attempt in the language being learned. The review is still read and shown
in the student's own language, exactly as the real session does it.

No microphone is involved: the "ready" handshake carries ``demo: true``, which
tells the frontend to turn the language buttons into "feed the next scripted
user line" triggers (a ``simulate_turn`` message) instead of recording. Each
button press plays one full exchange — user line, then AI answer — and once
the script's turns are exhausted the session wraps up on its own (analysis
status, homework, completion screen), simulating the whole arc end to end.
Replayed turns keep their recorded language regardless of which language
button advanced them; inline manuscript turns may carry en/es variants and
mirror the pressed button.

The manuscript (and the recorded session it points to) is re-read on every
connection, so editing files and refreshing the browser is enough — no server
restart needed. The mic-driven ``user_audio`` path is still handled for
protocol compatibility, but the demo frontend never sends it.
"""

import asyncio
import io
import json
import re
import shutil
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from session_core import (
    RECORDINGS_ROOT,
    Response,
    generate_audio_filename,
    save_transcript,
)

_REPO_ROOT = Path(__file__).parent.parent
MANUSCRIPT_PATH = Path(__file__).parent / "demo_manuscript.json"
DEMO_SESSION_NAME = "demo"

# Mirror web/session.py so the idle status lines, directions and filenames
# match exactly (spelled out rather than imported: the demo app deliberately
# pulls in none of the pipeline).
_PROMPT_TO_SPEAK = "Your turn — press a language button and speak."
_PROMPT_TO_TRANSLATE_TEMPLATE = (
    "Your turn to translate — press 🎙 {spoken} and say what the story means, "
    "sentence by sentence."
)
_PROMPT_TO_PRODUCE_TEMPLATE = (
    "Your turn to translate — press 🎙 {spoken} and say the story back in "
    "{spoken}, sentence by sentence."
)
INTO_NATIVE = "into_native"
INTO_TARGET = "into_target"
DIRECTIONS = (INTO_NATIVE, INTO_TARGET)
LESSON_FILENAME = "lesson.md"
REVIEW_FILENAME = "translation_review.md"

_DEFAULT_DELAYS = {
    "story_fetch": 1.5,
    "lesson_step": 0.25,
    "transcribe": 0.8,
    "generate": 1.1,
    "review": 2.0,
    "synthesize": 0.4,
    "analysis": 1.5,
}

# Status lines the real lesson build emits, so the demo shows the same
# progression (the real one is a minute of local inference).
_LESSON_STEPS = [
    "writing today's story",
    "glossing sentence 1 of 8",
    "glossing sentence 4 of 8",
    "glossing sentence 8 of 8",
    "writing the grammar notes",
]

# A stopped-immediately recording on the legacy mic path: anything shorter
# than ~0.25 s of 16-bit 48 kHz mono PCM demos the no-speech path.
_MIN_USER_AUDIO_BYTES = 24000

# Placeholder "speech" parameters (used only when no recorded audio exists).
_SAMPLE_RATE = 24000  # matches Kokoro's native rate
_SECONDS_PER_WORD = 0.14
_MAX_AUDIO_SECONDS = 7.0
# One tone per language so the placeholder audio is at least distinguishable;
# anything not listed falls back to 200 Hz at the call sites.
_BASE_PITCH_HZ = {"en": 210.0, "es": 175.0, "fr": 195.0}
_USER_PITCH_SCALE = 0.72  # the simulated student "voice" sits lower

# `**[23:53:34] You (en):** text` — the format save_transcript() writes.
_TRANSCRIPT_LINE = re.compile(r"\*\*\[\d\d:\d\d:\d\d\] (You|Tutor) \((\w+)\):\*\* (.+)")
# Trailing HHMMSS_mmm in per-turn WAV names, for chronological ordering.
_WAV_TIMESTAMP = re.compile(r"_(\d{6}_\d{3})\.wav$")


def _placeholder_speech_wav(text: str, lang: str, pitch_hz: float | None = None) -> bytes:
    """A gentle voice-like hum standing in for TTS output: pitch vibrato,
    syllable-rate amplitude pulses, and a length that scales with the text."""
    words = max(len(text.split()), 1)
    duration = min(0.4 + _SECONDS_PER_WORD * words, _MAX_AUDIO_SECONDS)
    n = int(duration * _SAMPLE_RATE)
    t = np.arange(n) / _SAMPLE_RATE

    pitch = pitch_hz if pitch_hz is not None else _BASE_PITCH_HZ.get(lang, 200.0)
    vibrato = 6.0 * np.sin(2 * np.pi * 5.0 * t)
    phase = 2 * np.pi * np.cumsum(pitch + vibrato) / _SAMPLE_RATE
    tone = (
        np.sin(phase)
        + 0.35 * np.sin(2 * phase)
        + 0.15 * np.sin(3 * phase)
    )

    # Syllable-ish pulsing plus a soft global fade in/out.
    syllables = 0.35 + 0.65 * (0.5 * (1 + np.sin(2 * np.pi * 3.4 * t))) ** 0.8
    fade = np.minimum(1.0, np.minimum(t, duration - t) / 0.06)
    audio = 0.22 * tone * syllables * fade

    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav_bytes)) as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return max(len(wav_bytes) - 44, 0) / (2 * _SAMPLE_RATE)


# ---------------------------------------------------------------------------
# Script loading. Both sources normalize turns to the same shape:
#   {"user":      {"variants": {lang: text, ...}, "audio_path": str | None},
#    "assistant": {"variants": {lang: text, ...}, "audio_path": str | None}}
# A recorded turn has exactly one variant (its recorded language) and a real
# audio file; an inline turn may offer several variants and no audio.
# ---------------------------------------------------------------------------

def _heading_and_body(md_path: Path) -> tuple[str, str]:
    """Split a markdown file into its first `# heading` and the rest."""
    lines = md_path.read_text(encoding="utf-8").strip().splitlines()
    heading = ""
    body_lines = []
    for line in lines:
        if not heading and line.startswith("# "):
            heading = line[2:].strip()
        else:
            body_lines.append(line)
    return heading, "\n".join(body_lines).strip()


def _session_wavs(session_dir: Path, author: str) -> list[Path]:
    def key(p: Path):
        m = _WAV_TIMESTAMP.search(p.name)
        return m.group(1) if m else p.name
    return sorted(session_dir.glob(f"{author}_*.wav"), key=key)


def _load_recorded_session(manuscript: dict, session_dir: Path):
    """Replace the manuscript's story/turns/homework with the recorded
    session's content. Raises if the essential files are missing."""
    story_title, story_body = _heading_and_body(session_dir / "story.md")
    article_title = ""
    if (session_dir / "article.md").is_file():
        article_title, _ = _heading_and_body(session_dir / "article.md")
    # Recorded folders name the narration for the language it is in; folders
    # recorded before that was true are all Spanish.
    story_audio = next(
        (p for p in sorted(session_dir.glob("story_*.wav"))), session_dir / "story_es.wav")
    manuscript["story"] = {
        "article_title": article_title or story_title,
        "story_title": story_title,
        "story": story_body,
        "audio_path": str(story_audio) if story_audio.is_file() else None,
    }

    transcript_text = (session_dir / "transcript.md").read_text(encoding="utf-8")
    entries = [m.groups() for m in map(_TRANSCRIPT_LINE.match, transcript_text.splitlines()) if m]
    user_wavs = _session_wavs(session_dir, "user")
    tutor_wavs = _session_wavs(session_dir, "assistant")

    turns = []
    pending_user = None
    user_i = tutor_i = 0
    for author, lang, text in entries:
        if author == "You":
            audio = user_wavs[user_i] if user_i < len(user_wavs) else None
            user_i += 1
            pending_user = {"variants": {lang: text.strip()},
                            "audio_path": str(audio) if audio else None}
        elif pending_user is not None:
            audio = tutor_wavs[tutor_i] if tutor_i < len(tutor_wavs) else None
            tutor_i += 1
            turns.append({
                "user": pending_user,
                "assistant": {"variants": {lang: text.strip()},
                              "audio_path": str(audio) if audio else None},
            })
            pending_user = None
    if not turns:
        raise ValueError(f"no conversation turns found in {session_dir / 'transcript.md'}")
    manuscript["turns"] = turns

    if (session_dir / "homework.md").is_file():
        manuscript["homework"] = (session_dir / "homework.md").read_text(encoding="utf-8")

    # A recorded translation challenge carries its own lesson and review; use
    # them in place of the manuscript's inline stand-ins.
    lesson_file = session_dir / LESSON_FILENAME
    if lesson_file.is_file():
        manuscript["lesson_markdown"] = lesson_file.read_text(encoding="utf-8")
    review_file = session_dir / REVIEW_FILENAME
    if review_file.is_file():
        manuscript.setdefault("translation", {})["review_markdown"] = \
            review_file.read_text(encoding="utf-8")


def _normalize_inline(manuscript: dict):
    """Bring the manuscript's inline fallback content to the same shape."""
    manuscript["turns"] = [
        {"user": {"variants": turn["user"], "audio_path": None},
         "assistant": {"variants": turn["assistant"], "audio_path": None}}
        for turn in manuscript.get("turns", [])
    ]
    story = manuscript.get("story")
    if story:
        story.setdefault("audio_path", None)


def _load_manuscript() -> dict:
    manuscript = json.loads(MANUSCRIPT_PATH.read_text(encoding="utf-8"))
    manuscript["delays_seconds"] = {
        **_DEFAULT_DELAYS,
        **manuscript.get("delays_seconds", {}),
    }
    session_rel = manuscript.get("session")
    if session_rel:
        try:
            _load_recorded_session(manuscript, (_REPO_ROOT / session_rel).resolve())
            return manuscript
        except Exception as e:
            print(f"Demo: could not replay recorded session {session_rel!r} ({e}); "
                  "falling back to the manuscript's inline content.")
    _normalize_inline(manuscript)
    return manuscript


def _pick_variant(part: dict, preferred_lang: str) -> tuple[str, str]:
    """Return (language, text): the pressed language's variant when the turn
    offers one, otherwise the turn's own (recorded) language."""
    variants = part["variants"]
    if preferred_lang in variants:
        return preferred_lang, variants[preferred_lang]
    return next(iter(variants.items()))


def _part_audio(part: dict, text: str, lang: str, pitch_hz: float | None = None) -> bytes:
    """The recorded WAV for this line if the script has one, else a
    placeholder tone."""
    if part.get("audio_path"):
        try:
            return Path(part["audio_path"]).read_bytes()
        except OSError:
            pass
    return _placeholder_speech_wav(text, lang, pitch_hz=pitch_hz)


class _SessionEnded(Exception):
    """Raised when the client explicitly quits before a phase completes."""


class DemoSessionOrchestrator:
    """Same constructor signature and run() contract as SessionOrchestrator,
    so web/server.py can swap one for the other."""

    def __init__(self, ws: WebSocket, app_state, whisper_lock: asyncio.Lock, user):
        self.ws = ws
        # Demo mode keeps its hardcoded recordings/demo folder, but still
        # honours the picked user's language pair so the buttons and prompts
        # read the same as they would in a real session.
        self.user = user
        self.target = user.target
        self.native = user.native
        self.direction = INTO_NATIVE
        # Which language the scripted translation is spoken in; set for real
        # when a challenge starts.
        self.translate_lang = self.native.code
        self.prompt_to_translate = _PROMPT_TO_TRANSLATE_TEMPLATE.format(
            spoken=self.native.endonym)
        self.manuscript = _load_manuscript()
        self.delays = self.manuscript["delays_seconds"]
        self.session_dir: Path | None = None
        self.session_name: str | None = None
        self.responses: list[Response] = []
        self.turn_index = 0
        self.mode: str | None = None
        self.stage = "converse"
        # Set once the challenge's story has played: the next button press
        # plays the scripted translation rather than a conversation turn.
        self.awaiting_translation = False
        self.awaiting_mode = True
        # Duration of the last reply audio, so the auto-wrap-up after the
        # final turn waits for its client-side playback to finish.
        self._last_reply_seconds = 0.0

    async def _send(self, type_: str, **payload):
        await self.ws.send_json({"type": type_, **payload})

    async def _status(self, state: str, message: str):
        await self._send("status", state=state, message=message)

    async def _set_stage(self, stage: str, prompt: str, language: str | None = None):
        self.stage = stage
        await self._send("stage", stage=stage, prompt=prompt, language=language)

    async def run(self):
        try:
            # demo=True tells the frontend to simulate turns instead of
            # recording from the microphone.
            await self._send(
                "ready", demo=True,
                native={"code": self.native.code, "label": self.native.endonym},
                target={"code": self.target.code, "label": self.target.endonym},
            )
            await self._command_loop()
        except (WebSocketDisconnect, _SessionEnded):
            pass
        except Exception as e:
            try:
                await self._send("error", message=f"Internal server error: {e}")
            except Exception:
                pass
        finally:
            await self._finish()

    async def _command_loop(self):
        while True:
            message = await self.ws.receive_json()
            msg_type = message.get("type")
            if msg_type == "end_session":
                break
            elif msg_type in {"start_talk", "start_challenge"}:
                if not self.awaiting_mode:
                    continue
                self.awaiting_mode = False
                if msg_type == "start_talk":
                    await self._start_talk()
                else:
                    await self._start_challenge(message.get("direction"))
            elif msg_type in {"user_audio", "simulate_turn"}:
                if self.mode is None:
                    if msg_type == "user_audio":
                        # The header is always followed by one binary frame;
                        # consume it to stay in sync.
                        await self.ws.receive_bytes()
                    await self._send("status", state="idle", message="Pick a mode first.")
                    continue
                if msg_type == "user_audio":
                    await self._handle_turn(message)
                elif self.awaiting_translation:
                    await self._play_scripted_translation()
                    continue
                else:
                    await self._play_scripted_turn(
                        message.get("language", self.native.code))
                if self.turn_index >= len(self.manuscript["turns"]):
                    # Script exhausted — let the last reply finish playing
                    # client-side, then wrap the session up automatically.
                    await asyncio.sleep(self._last_reply_seconds + 0.3)
                    break

    async def _wait_for(self, expected_type: str):
        while True:
            message = await self.ws.receive_json()
            msg_type = message.get("type")
            if msg_type == expected_type:
                return message
            if msg_type == "end_session":
                raise _SessionEnded()

    def _ensure_session_dir(self):
        """Use a fixed recordings/demo folder, wiped per connection, so demo
        runs never pile up next to real session recordings."""
        if self.session_dir is None:
            demo_dir = Path(RECORDINGS_ROOT) / DEMO_SESSION_NAME
            shutil.rmtree(demo_dir, ignore_errors=True)
            demo_dir.mkdir(parents=True, exist_ok=True)
            self.session_dir = demo_dir
            self.session_name = DEMO_SESSION_NAME

    # -- Entry points --------------------------------------------------------

    async def _start_talk(self):
        if self.mode is not None:
            return
        self.mode = "talk"
        self._ensure_session_dir()
        await self._send("mode", mode="talk", session_name=self.session_name)
        await self._status("idle", _PROMPT_TO_SPEAK)

    async def _start_challenge(self, direction: str | None = None):
        if self.mode is not None:
            return
        self.mode = "challenge"
        if direction in DIRECTIONS:
            self.direction = direction
        reverse = self.direction == INTO_TARGET
        self.translate_lang = self.target.code if reverse else self.native.code
        self.prompt_to_translate = (
            _PROMPT_TO_PRODUCE_TEMPLATE if reverse else _PROMPT_TO_TRANSLATE_TEMPLATE
        ).format(spoken=(self.target if reverse else self.native).endonym)
        self._ensure_session_dir()
        await self._send("mode", mode="challenge", session_name=self.session_name,
                         direction=self.direction)

        await self._status(
            "thinking",
            "Fetching today's Wikipedia article and building your translation challenge…")
        await asyncio.sleep(self.delays["story_fetch"])
        for i, label in enumerate(_LESSON_STEPS, start=1):
            await self._status(
                "thinking",
                f"Building today's challenge — {label}… ({i}/{len(_LESSON_STEPS)})")
            await asyncio.sleep(self.delays["lesson_step"])

        prompt = self._prompt_side()
        if prompt:
            await self._status("speaking", "Reading today's story aloud…")
            audio_bytes = _part_audio(
                prompt, f"{prompt['title']}. {prompt['text']}", prompt["lang"])
            story_filename = f"story_{prompt['lang']}.wav"
            (self.session_dir / story_filename).write_bytes(audio_bytes)
            # Mirror web/session.py: the story is a normal assistant bubble
            # (sent after the WAV exists — the bubble's audio element is
            # the single playback source and fetches it from the session
            # route; tts_audio is just the auto-play cue).
            await self._send(
                "transcript",
                author="assistant",
                language=prompt["lang"],
                text=f"{prompt['title']}\n\n{prompt['text']}",
                audio_filename=story_filename,
            )
            await self._send("tts_audio", turn="story")
            await self._wait_for("tts_playback_done")

        # lesson.md holds the answer key, so it stays unlinked until the
        # translation has been marked — same as the real session.
        self._write_lesson_file()
        self.awaiting_translation = True
        await self._set_stage("translate", self.prompt_to_translate,
                              language=self.translate_lang)
        await self._status("idle", self.prompt_to_translate)

    def _translation_script(self) -> dict:
        """The scripted translation for whichever direction is running."""
        key = "translation_into_target" if self.direction == INTO_TARGET else "translation"
        return self.manuscript.get(key) or {}

    def _prompt_side(self) -> dict | None:
        """What gets narrated: the target-language story, or — reversed — the
        native-language text the manuscript gives to translate back. Falls back
        to the story so a manuscript without a reverse block still demos the
        layout, just in the wrong language."""
        story = self.manuscript.get("story")
        if self.direction == INTO_TARGET:
            script = self._translation_script()
            if script.get("prompt"):
                return {"lang": self.native.code,
                        "title": script.get("prompt_title", ""),
                        "text": script["prompt"],
                        "audio_path": None}
        if not story:
            return None
        return {"lang": self.target.code, "title": story["story_title"],
                "text": story["story"], "audio_path": story.get("audio_path")}

    def _write_lesson_file(self):
        """The recorded session's lesson when there is one, otherwise the story
        prose under its title — enough for the link to open something real."""
        markdown = self.manuscript.get("lesson_markdown")
        if not markdown:
            story = self.manuscript.get("story") or {}
            markdown = f"# {story.get('story_title', 'Lesson')}\n\n{story.get('story', '')}\n"
        (self.session_dir / LESSON_FILENAME).write_text(markdown, encoding="utf-8")

    # -- Per-turn conversation ----------------------------------------------

    async def _handle_turn(self, header: dict):
        """Mic-driven path, kept for protocol compatibility: a user_audio
        header followed by one binary frame of recorded WAV."""
        language = header.get("language", self.native.code)
        audio_bytes = await self.ws.receive_bytes()

        if len(audio_bytes) < _MIN_USER_AUDIO_BYTES:
            await self._send("no_speech")
            await self._status("idle", "No speech detected — try again.")
            return

        if self.awaiting_translation:
            await self._play_scripted_translation(audio_bytes)
            return

        await self._play_scripted_turn(language, audio_bytes)

    async def _play_scripted_translation(self, user_audio_bytes: bytes | None = None):
        """The challenge's one scripted special turn: the student's spoken
        translation — into their own language, or into the one they are
        learning when the challenge is reversed — then the marked review, which
        is in their own language either way."""
        self.awaiting_translation = False
        script = self._translation_script()
        student_text = script.get("student", "(no scripted translation)")
        review_text = script.get("review", "(no scripted review)")
        spoken_lang = self.translate_lang

        if user_audio_bytes is None:
            user_audio_bytes = _placeholder_speech_wav(
                student_text, spoken_lang,
                pitch_hz=_BASE_PITCH_HZ.get(spoken_lang, 200.0) * _USER_PITCH_SCALE)
        audio_path = generate_audio_filename(self.session_dir, "user", spoken_lang)
        Path(audio_path).write_bytes(user_audio_bytes)

        await self._status("thinking", "Transcribing what you said…")
        user_start = datetime.now()
        await asyncio.sleep(self.delays["transcribe"])
        self.responses.append(Response(
            author="user", language=spoken_lang, text=student_text,
            timestamp=datetime.now(), audio_sample=audio_path,
        ))
        await self._send(
            "transcript", author="user", language=spoken_lang, text=student_text,
            audio_filename=Path(audio_path).name,
            processing_ms=int((datetime.now() - user_start).total_seconds() * 1000),
        )

        await self._status("thinking", "Marking your translation against the story…")
        review_start = datetime.now()
        await asyncio.sleep(self.delays["review"])
        await self._status("thinking", "Synthesizing speech…")
        await asyncio.sleep(self.delays["synthesize"])

        review_audio_path = generate_audio_filename(
            self.session_dir, "assistant", self.native.code)
        review_audio = _placeholder_speech_wav(review_text, self.native.code)
        Path(review_audio_path).write_bytes(review_audio)
        self._last_reply_seconds = _wav_duration_seconds(review_audio)
        self.responses.append(Response(
            author="assistant", language=self.native.code, text=review_text,
            timestamp=datetime.now(), audio_sample=review_audio_path,
        ))
        (self.session_dir / REVIEW_FILENAME).write_text(
            script.get("review_markdown") or f"# Translation review\n\n{review_text}\n",
            encoding="utf-8")

        await self._set_stage("converse", _PROMPT_TO_SPEAK)
        await self._send(
            "transcript", author="assistant", language=self.native.code, text=review_text,
            audio_filename=Path(review_audio_path).name,
            processing_ms=int((datetime.now() - review_start).total_seconds() * 1000),
        )
        await self._send("tts_audio", turn="reply")
        await self._send("artifact", filename=LESSON_FILENAME,
                         label="Open the lesson (story + literal translation)")
        await self._send("artifact", filename=REVIEW_FILENAME,
                         label="Open the full translation review")

    async def _play_scripted_turn(self, language: str, user_audio_bytes: bytes | None = None):
        """Play the next scripted exchange — user line, then assistant answer
        — with the same statuses and timing the real pipeline produces.
        Recorded turns replay their original text, language, and audio; inline
        turns follow the pressed language button and get placeholder tones."""
        turn = self.manuscript["turns"][self.turn_index]
        self.turn_index += 1
        user_lang, user_text = _pick_variant(turn["user"], language)
        asst_lang, asst_text = _pick_variant(turn["assistant"], language)

        if user_audio_bytes is None:
            user_audio_bytes = _part_audio(
                turn["user"], user_text, user_lang,
                pitch_hz=_BASE_PITCH_HZ.get(user_lang, 200.0) * _USER_PITCH_SCALE,
            )
        audio_path = generate_audio_filename(self.session_dir, "user", user_lang)
        Path(audio_path).write_bytes(user_audio_bytes)

        await self._status("thinking", "Transcribing what you said…")
        user_start = datetime.now()
        await asyncio.sleep(self.delays["transcribe"])
        user_processing_ms = int((datetime.now() - user_start).total_seconds() * 1000)

        self.responses.append(Response(
            author="user", language=user_lang, text=user_text,
            timestamp=datetime.now(), audio_sample=audio_path,
        ))
        await self._send(
            "transcript",
            author="user",
            language=user_lang,
            text=user_text,
            audio_filename=Path(audio_path).name,
            processing_ms=user_processing_ms,
        )

        await self._status("thinking", "Generating a response…")
        assistant_start = datetime.now()
        await asyncio.sleep(self.delays["generate"])

        await self._status("thinking", "Synthesizing speech…")
        await asyncio.sleep(self.delays["synthesize"])
        assistant_audio_path = generate_audio_filename(self.session_dir, "assistant", asst_lang)
        reply_audio = _part_audio(turn["assistant"], asst_text, asst_lang)
        Path(assistant_audio_path).write_bytes(reply_audio)
        self._last_reply_seconds = _wav_duration_seconds(reply_audio)
        self.responses.append(Response(
            author="assistant", language=asst_lang, text=asst_text,
            timestamp=datetime.now(), audio_sample=assistant_audio_path,
        ))

        assistant_processing_ms = int((datetime.now() - assistant_start).total_seconds() * 1000)
        await self._send(
            "transcript",
            author="assistant",
            language=asst_lang,
            text=asst_text,
            audio_filename=Path(assistant_audio_path).name,
            processing_ms=assistant_processing_ms,
        )
        # Cue the client to auto-play the reply bubble's audio element.
        await self._send("tts_audio", turn="reply")

    # -- Wrap-up -------------------------------------------------------------

    async def _finish(self):
        if self.session_dir is None or not self.responses:
            try:
                await self._send("done", transcript_path=None, homework_path=None)
            except (WebSocketDisconnect, RuntimeError):
                pass
            return

        await self._status("thinking", "Analyzing your session and writing homework…")
        await asyncio.sleep(self.delays["analysis"])
        transcript_path = save_transcript(self.responses, self.session_dir)

        homework_path = None
        if self.manuscript.get("homework"):
            homework_path = self.session_dir / "homework.md"
            homework_path.write_text(self.manuscript["homework"], encoding="utf-8")

        artifacts = {
            key: name for key, name in (("lesson_filename", LESSON_FILENAME),
                                        ("review_filename", REVIEW_FILENAME))
            if (self.session_dir / name).is_file()
        }
        try:
            await self._send(
                "done",
                session_name=self.session_name,
                transcript_filename=transcript_path.name,
                homework_filename=homework_path.name if homework_path else None,
                **artifacts,
            )
        except (WebSocketDisconnect, RuntimeError):
            pass
