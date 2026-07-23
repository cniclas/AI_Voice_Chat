"""SessionOrchestrator: drives one tutoring session over a single WebSocket
connection.

Unlike the terminal UI (`main.py`), which runs the five-phase arc top to
bottom, the browser session is *command-driven*. On connect the orchestrator
only signals readiness; nothing happens until the user picks an entry point:

- ``start_talk``  — a plain push-to-talk conversation, no story preamble.
- ``start_story`` — fetch today's Wikipedia "on this day" story, narrate it,
  then converse about it.

Both land in the same per-turn conversation handling, and both end with the
analyze/homework phase when the user exits. Status updates are streamed to the
client throughout so the user always sees what the backend is doing
(transcribing, generating, synthesizing, …).
"""

import asyncio
from datetime import datetime
from pathlib import Path

import requests
from fastapi import WebSocket, WebSocketDisconnect

import curriculum
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

_PROMPT_TO_SPEAK = "Your turn — press a language button and speak."


class _SessionEnded(Exception):
    """Raised when the client explicitly quits before a phase completes."""


class SessionOrchestrator:
    def __init__(self, ws: WebSocket, app_state, whisper_lock: asyncio.Lock):
        self.ws = ws
        self.app_state = app_state
        self.whisper_model = None
        self.whisper_lock = whisper_lock
        self.session_dir: Path | None = None
        self.session_name: str | None = None
        self.profile: dict | None = None
        self.llm_history: list = []
        self.responses: list[Response] = []
        self.setup_state: dict = {}
        self.mode: str | None = None  # "talk" or "story" once a conversation starts
        self.awaiting_mode = True

    async def _send(self, type_: str, **payload):
        await self.ws.send_json({"type": type_, **payload})

    async def _status(self, state: str, message: str):
        """Push a combined avatar-state + human-readable status line."""
        await self._send("status", state=state, message=message)

    async def run(self):
        try:
            await self._status("loading", "Loading Whisper and Kokoro…")
            # Wait for the background model loader so the backend does not
            # claim readiness until the model is actually available.
            await self.app_state.whisper_ready.wait()
            self.whisper_model = self.app_state.whisper_model
            await self._send("ready")
            await self._command_loop()
        except (WebSocketDisconnect, _SessionEnded):
            # Normal session termination.
            pass
        except Exception as e:
            # Unexpected server error — surface to client and continue to
            # the cleanup phase so we don't leave temporary files behind.
            try:
                await self._send("error", message=f"Internal server error: {e}")
            except Exception:
                pass
        finally:
            await self._analyze_and_persist()

    async def _command_loop(self):
        """Dispatch client commands until the session ends."""
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
            elif msg_type == "user_audio":
                if self.mode is None:
                    await self._send("status", state="idle", message="Pick a mode first.")
                    continue
                await self._handle_turn(message)
            # Unknown message types are ignored (forward-compatible).

    async def _wait_for(self, expected_type: str):
        """Block until a specific client message arrives, treating an
        explicit end_session as an early-exit signal."""
        while True:
            message = await self.ws.receive_json()
            msg_type = message.get("type")
            if msg_type == expected_type:
                return message
            if msg_type == "end_session":
                raise _SessionEnded()

    def _ensure_session_dir(self):
        if self.session_dir is None:
            self.session_dir = create_session_dir()
            self.session_name = self.session_dir.name

    # -- Entry points --------------------------------------------------------

    async def _start_talk(self):
        """Free conversation: no story, base tutoring prompt."""
        if self.mode is not None:
            return
        self.mode = "talk"
        self._ensure_session_dir()
        if self.profile is None:
            self.profile = await asyncio.to_thread(curriculum.load_profile)
        self.llm_history = [{"role": "system", "content": build_system_prompt(None)}]
        await self._send("mode", mode="talk", session_name=self.session_name)
        await self._status("idle", _PROMPT_TO_SPEAK)

    async def _start_story(self):
        """Fetch today's Wikipedia story, narrate it, then converse about it."""
        if self.mode is not None:
            return
        self.mode = "story"
        self._ensure_session_dir()
        await self._send("mode", mode="story", session_name=self.session_name)

        await self._status("thinking",
                           "Fetching today's Wikipedia story and writing your lesson… (this can take a moment)")
        self.setup_state = await asyncio.to_thread(
            session_setup_graph.invoke, {"session_dir": str(self.session_dir)})
        self.profile = self.setup_state["profile"]

        if self.setup_state.get("setup_failed"):
            await self._send("error", message=self.setup_state["setup_failed"])
        daily = daily_story_from_setup_state(self.setup_state)

        if daily:
            await self._status("speaking", "Reading today's story aloud…")
            await asyncio.to_thread(
                synthesize,
                f"{daily['story_title']}. {daily['story']}",
                lang="es",
                output_file=str(self.session_dir / "story_es.wav"),
                play=False,
            )
            # The story arrives as a normal assistant chat bubble, so it gets
            # the same audio control as every other reply. Sent only after
            # synthesize() has written story_es.wav: the bubble's audio
            # element is the single playback source, and it fetches that file
            # from the session route. tts_audio is just the auto-play cue.
            await self._send(
                "transcript",
                author="assistant",
                language="es",
                text=f"{daily['story_title']}\n\n{daily['story']}",
                audio_filename="story_es.wav",
            )
            await self._send("tts_audio", turn="story")
            await self._wait_for("tts_playback_done")

        self.llm_history = [{"role": "system", "content": build_system_prompt(daily)}]
        await self._status("idle", _PROMPT_TO_SPEAK)

    # -- Per-turn conversation ----------------------------------------------

    async def _handle_turn(self, header: dict):
        # A user_audio header is always followed by one binary frame; consume
        # it even if we're not in a conversation yet, to stay in sync.
        language = header.get("language", "en")
        audio_bytes = await self.ws.receive_bytes()
        if self.mode is None:
            return

        audio_path = generate_audio_filename(self.session_dir, "user", language)
        Path(audio_path).write_bytes(audio_bytes)

        await self._status("thinking", "Transcribing what you said…")
        user_start = datetime.now()
        async with self.whisper_lock:
            user_text = await asyncio.to_thread(
                transcribe_audio, audio_path, self.whisper_model, language)
        user_processing_ms = int((datetime.now() - user_start).total_seconds() * 1000)
        if not user_text:
            await self._send("no_speech")
            await self._status("idle", "No speech detected — try again.")
            return

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
        try:
            response_text = await asyncio.to_thread(
                query_llm, user_text, language, self.llm_history)
            assistant_audio_path = generate_audio_filename(self.session_dir, "assistant", language)
            self.responses.append(Response(
                author="assistant", language=language, text=response_text,
                timestamp=datetime.now(), audio_sample=assistant_audio_path,
            ))

            await self._status("thinking", "Synthesizing speech…")
            await asyncio.to_thread(
                synthesize, response_text, lang=language,
                output_file=assistant_audio_path, play=False,
            )
        except requests.RequestException as e:
            await self._send("error", message=f"Ollama error: {e}. Make sure Ollama is running (ollama serve).")
            await self._status("idle", _PROMPT_TO_SPEAK)
            return

        assistant_processing_ms = int((datetime.now() - assistant_start).total_seconds() * 1000)
        await self._send(
            "transcript",
            author="assistant",
            language=language,
            text=response_text,
            audio_filename=Path(assistant_audio_path).name,
            processing_ms=assistant_processing_ms,
        )
        # Cue the client to auto-play the reply bubble's audio element (the
        # WAV itself is fetched from the session route, not sent over the WS).
        await self._send("tts_audio", turn="reply")

    # -- Wrap-up -------------------------------------------------------------

    async def _analyze_and_persist(self):
        if self.session_dir is None or not self.responses:
            try:
                await self._send("done", transcript_path=None, homework_path=None)
            except (WebSocketDisconnect, RuntimeError):
                pass
            if self.session_dir is not None:
                try:
                    self.session_dir.rmdir()  # Remove the session folder if still empty
                except OSError:
                    pass
            return

        await self._status("thinking", "Analyzing your session and writing homework…")
        transcript_path = save_transcript(self.responses, self.session_dir)
        transcript_text = format_transcript_for_lesson(self.responses)

        result = await asyncio.to_thread(session_analysis_graph.invoke, {
            "session_dir": str(self.session_dir),
            "profile": self.profile,
            "transcript_text": transcript_text,
            "story": self.setup_state.get("story"),
        })

        homework_path = str(self.session_dir / "homework.md") if result.get("homework") else None
        try:
            await self._send(
                "done",
                session_name=self.session_name,
                transcript_filename=transcript_path.name,
                homework_filename=Path(homework_path).name if homework_path else None,
                lesson_filename=Path(homework_path).name if homework_path else None,
            )
        except (WebSocketDisconnect, RuntimeError):
            pass
