"""DemoSessionOrchestrator: a stand-in for SessionOrchestrator that speaks the
exact same WebSocket protocol but never touches Whisper, Ollama, Kokoro or
Wikipedia. Every button press advances a predefined manuscript
(web/demo_manuscript.json) instead, so the browser UI can be designed and
iterated on with instant startup and zero model downloads.

No microphone is involved: the "ready" handshake carries ``demo: true``, which
tells the frontend to turn the language buttons into "feed the next scripted
user line" triggers (a ``simulate_turn`` message) instead of recording. Each
button press plays one full exchange — scripted user line, then scripted AI
answer — and once the manuscript's turns are exhausted the session wraps up
on its own (analysis status, homework, completion screen), simulating the
whole arc end to end.

What the demo preserves so the *feel* matches the real app:

- The full message protocol (status/mode/story/transcript/tts_audio/done …).
- Timing: each phase sleeps a configurable delay (see "delays_seconds" in the
  manuscript) so the thinking/speaking avatar states are visible.
- Audio: turns synthesize a short voice-like placeholder tone whose length
  scales with the text (lower pitch for the "user" voice), so the speaking
  avatar state and the per-message replay controls work.
- Artifacts: transcript.md and homework.md are written to recordings/demo so
  the completion-screen links resolve.
- The manuscript is re-read on every connection, so editing the JSON and
  refreshing the browser is enough — no server restart needed.

The mic-driven ``user_audio`` path is still handled for protocol
compatibility, but the demo frontend never sends it.
"""

import asyncio
import io
import json
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

MANUSCRIPT_PATH = Path(__file__).parent / "demo_manuscript.json"
DEMO_SESSION_NAME = "demo"

# Mirrors web/session.py so the idle status line reads identically.
_PROMPT_TO_SPEAK = "Your turn — press a language button and speak."

_DEFAULT_DELAYS = {
    "story_fetch": 1.5,
    "transcribe": 0.8,
    "generate": 1.1,
    "synthesize": 0.4,
    "analysis": 1.5,
}

# A stopped-immediately recording: anything shorter than ~0.25 s of 16-bit
# 48 kHz mono PCM demos the no-speech path.
_MIN_USER_AUDIO_BYTES = 24000

# Placeholder "speech" parameters.
_SAMPLE_RATE = 24000  # matches Kokoro's native rate
_SECONDS_PER_WORD = 0.14
_MAX_AUDIO_SECONDS = 7.0
_BASE_PITCH_HZ = {"en": 210.0, "es": 175.0}
_USER_PITCH_SCALE = 0.72  # the simulated student "voice" sits lower


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


def _load_manuscript() -> dict:
    manuscript = json.loads(MANUSCRIPT_PATH.read_text(encoding="utf-8"))
    manuscript["delays_seconds"] = {
        **_DEFAULT_DELAYS,
        **manuscript.get("delays_seconds", {}),
    }
    return manuscript


class _SessionEnded(Exception):
    """Raised when the client explicitly quits before a phase completes."""


class DemoSessionOrchestrator:
    """Same constructor signature and run() contract as SessionOrchestrator,
    so web/server.py can swap one for the other."""

    def __init__(self, ws: WebSocket, app_state, whisper_lock: asyncio.Lock):
        self.ws = ws
        self.manuscript = _load_manuscript()
        self.delays = self.manuscript["delays_seconds"]
        self.session_dir: Path | None = None
        self.session_name: str | None = None
        self.responses: list[Response] = []
        self.turn_index = 0
        self.mode: str | None = None
        self.awaiting_mode = True
        # Duration of the last reply tone, so the auto-wrap-up after the final
        # manuscript turn waits for its client-side playback to finish.
        self._last_reply_seconds = 0.0

    async def _send(self, type_: str, **payload):
        await self.ws.send_json({"type": type_, **payload})

    async def _status(self, state: str, message: str):
        await self._send("status", state=state, message=message)

    async def run(self):
        try:
            # demo=True tells the frontend to simulate turns instead of
            # recording from the microphone.
            await self._send("ready", demo=True)
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
            elif msg_type in {"start_talk", "start_story"}:
                if not self.awaiting_mode:
                    continue
                self.awaiting_mode = False
                if msg_type == "start_talk":
                    await self._start_talk()
                else:
                    await self._start_story()
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
                else:
                    await self._play_scripted_turn(message.get("language", "en"))
                if self.turn_index >= len(self.manuscript["turns"]):
                    # Manuscript exhausted — let the last reply finish playing
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

    async def _start_story(self):
        if self.mode is not None:
            return
        self.mode = "story"
        self._ensure_session_dir()
        await self._send("mode", mode="story", session_name=self.session_name)

        await self._status("thinking",
                           "Fetching today's Wikipedia story and writing your lesson… (this can take a moment)")
        await asyncio.sleep(self.delays["story_fetch"])

        story = self.manuscript.get("story")
        await self._send(
            "story",
            article_title=story["article_title"] if story else None,
            story_title=story["story_title"] if story else None,
            story=story["story"] if story else None,
        )

        if story:
            await self._status("speaking", "Reading today's story aloud…")
            audio_bytes = _placeholder_speech_wav(
                f"{story['story_title']}. {story['story']}", "es")
            (self.session_dir / "story_es.wav").write_bytes(audio_bytes)
            await self._send("tts_audio", turn="story")
            await self.ws.send_bytes(audio_bytes)
            await self._wait_for("tts_playback_done")

        await self._status("idle", _PROMPT_TO_SPEAK)

    # -- Per-turn conversation ----------------------------------------------

    async def _handle_turn(self, header: dict):
        """Mic-driven path, kept for protocol compatibility: a user_audio
        header followed by one binary frame of recorded WAV."""
        language = header.get("language", "en")
        audio_bytes = await self.ws.receive_bytes()

        if len(audio_bytes) < _MIN_USER_AUDIO_BYTES:
            await self._send("no_speech")
            await self._status("idle", "No speech detected — try again.")
            return

        await self._play_scripted_turn(language, audio_bytes)

    async def _play_scripted_turn(self, language: str, user_audio_bytes: bytes | None = None):
        """Play the next manuscript exchange: the scripted user line, then the
        scripted assistant answer, with the same statuses and timing the real
        pipeline produces. Without mic audio (the simulate_turn path) the user
        line gets a lower-pitched placeholder tone of its own."""
        turns = self.manuscript["turns"]
        turn = turns[self.turn_index]
        self.turn_index += 1
        user_text = turn["user"].get(language) or turn["user"]["en"]
        assistant_text = turn["assistant"].get(language) or turn["assistant"]["en"]

        if user_audio_bytes is None:
            user_audio_bytes = _placeholder_speech_wav(
                user_text, language,
                pitch_hz=_BASE_PITCH_HZ.get(language, 200.0) * _USER_PITCH_SCALE,
            )
        audio_path = generate_audio_filename(self.session_dir, "user", language)
        Path(audio_path).write_bytes(user_audio_bytes)

        await self._status("thinking", "Transcribing what you said…")
        user_start = datetime.now()
        await asyncio.sleep(self.delays["transcribe"])
        user_processing_ms = int((datetime.now() - user_start).total_seconds() * 1000)

        self.responses.append(Response(
            author="user", language=language, text=user_text,
            timestamp=datetime.now(), audio_sample=audio_path,
        ))
        await self._send(
            "transcript",
            author="user",
            language=language,
            text=user_text,
            audio_filename=Path(audio_path).name,
            processing_ms=user_processing_ms,
        )

        await self._status("thinking", "Generating a response…")
        assistant_start = datetime.now()
        await asyncio.sleep(self.delays["generate"])

        await self._status("thinking", "Synthesizing speech…")
        await asyncio.sleep(self.delays["synthesize"])
        assistant_audio_path = generate_audio_filename(self.session_dir, "assistant", language)
        reply_audio = _placeholder_speech_wav(assistant_text, language)
        Path(assistant_audio_path).write_bytes(reply_audio)
        self._last_reply_seconds = max(len(reply_audio) - 44, 0) / (2 * _SAMPLE_RATE)
        self.responses.append(Response(
            author="assistant", language=language, text=assistant_text,
            timestamp=datetime.now(), audio_sample=assistant_audio_path,
        ))

        assistant_processing_ms = int((datetime.now() - assistant_start).total_seconds() * 1000)
        await self._send(
            "transcript",
            author="assistant",
            language=language,
            text=assistant_text,
            audio_filename=Path(assistant_audio_path).name,
            processing_ms=assistant_processing_ms,
        )
        await self._send("tts_audio", turn="reply")
        await self.ws.send_bytes(reply_audio)

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

        try:
            await self._send(
                "done",
                session_name=self.session_name,
                transcript_filename=transcript_path.name,
                homework_filename=homework_path.name if homework_path else None,
                lesson_filename=homework_path.name if homework_path else None,
            )
        except (WebSocketDisconnect, RuntimeError):
            pass
