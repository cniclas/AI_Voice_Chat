"""SessionOrchestrator: drives one tutoring session over a single WebSocket
connection, mirroring main.py's five-phase arc (prepare/narrate/converse/
analyze/homework) but event-driven instead of blocking on terminal keypresses
and local audio playback.
"""

import asyncio
from datetime import datetime
from pathlib import Path

import requests
from fastapi import WebSocket, WebSocketDisconnect

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


class _SessionEnded(Exception):
    """Raised when the client explicitly quits before a phase completes."""


class SessionOrchestrator:
    def __init__(self, ws: WebSocket, whisper_model, whisper_lock: asyncio.Lock):
        self.ws = ws
        self.whisper_model = whisper_model
        self.whisper_lock = whisper_lock
        self.session_dir: Path | None = None
        self.profile: dict | None = None
        self.llm_history: list = []
        self.responses: list[Response] = []
        self.setup_state: dict = {}

    async def _send(self, type_: str, **payload):
        await self.ws.send_json({"type": type_, **payload})

    async def run(self):
        try:
            await self._prepare_and_narrate()
            await self._converse_loop()
        except (WebSocketDisconnect, _SessionEnded):
            pass
        finally:
            await self._analyze_and_persist()

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

    async def _prepare_and_narrate(self):
        self.session_dir = create_session_dir()
        await self._send("phase", phase="prepare", status="running")

        self.setup_state = await asyncio.to_thread(
            session_setup_graph.invoke, {"session_dir": str(self.session_dir)})
        self.profile = self.setup_state["profile"]

        if self.setup_state.get("setup_failed"):
            await self._send("error", message=self.setup_state["setup_failed"])
        daily = daily_story_from_setup_state(self.setup_state)

        await self._send(
            "story",
            article_title=daily["article_title"] if daily else None,
            story_title=daily["story_title"] if daily else None,
            story=daily["story"] if daily else None,
        )
        await self._send("phase", phase="prepare", status="done")

        if daily:
            await self._send("phase", phase="narrate", status="running")
            audio_bytes = await asyncio.to_thread(
                synthesize,
                f"{daily['story_title']}. {daily['story']}",
                lang="es",
                output_file=str(self.session_dir / "story_es.wav"),
                play=False,
            )
            await self._send("tts_audio", turn="story")
            await self.ws.send_bytes(audio_bytes)
            await self._wait_for("tts_playback_done")
            await self._send("phase", phase="narrate", status="done")

        self.llm_history = [{"role": "system", "content": build_system_prompt(daily)}]
        await self._send("phase", phase="converse", status="running")

    async def _converse_loop(self):
        while True:
            header = await self.ws.receive_json()
            msg_type = header.get("type")
            if msg_type == "end_session":
                break
            if msg_type != "user_audio":
                continue

            language = header["language"]
            audio_bytes = await self.ws.receive_bytes()
            audio_path = generate_audio_filename(self.session_dir, "user", language)
            Path(audio_path).write_bytes(audio_bytes)

            async with self.whisper_lock:
                user_text = await asyncio.to_thread(
                    transcribe_audio, audio_path, self.whisper_model, language)
            if not user_text:
                await self._send("no_speech")
                continue

            self.responses.append(Response(
                author="user", language=language, text=user_text,
                timestamp=datetime.now(), audio_sample=audio_path,
            ))
            await self._send("transcript", author="user", language=language, text=user_text)

            try:
                response_text = await asyncio.to_thread(
                    query_llm, user_text, language, self.llm_history)
            except requests.RequestException as e:
                await self._send("error", message=f"Ollama error: {e}. Make sure Ollama is running (ollama serve).")
                continue

            assistant_audio_path = generate_audio_filename(self.session_dir, "assistant", language)
            self.responses.append(Response(
                author="assistant", language=language, text=response_text,
                timestamp=datetime.now(), audio_sample=assistant_audio_path,
            ))
            await self._send("transcript", author="assistant", language=language, text=response_text)

            audio_bytes = await asyncio.to_thread(
                synthesize, response_text, lang=language,
                output_file=assistant_audio_path, play=False,
            )
            await self._send("tts_audio", turn="reply")
            await self.ws.send_bytes(audio_bytes)

    async def _analyze_and_persist(self):
        if not self.responses:
            await self._send("done", transcript_path=None, homework_path=None)
            try:
                self.session_dir.rmdir()  # Remove the empty session folder
            except OSError:
                pass
            return

        await self._send("phase", phase="analyze", status="running")
        transcript_path = save_transcript(self.responses, self.session_dir)
        transcript_text = format_transcript_for_lesson(self.responses)

        result = await asyncio.to_thread(session_analysis_graph.invoke, {
            "session_dir": str(self.session_dir),
            "profile": self.profile,
            "transcript_text": transcript_text,
            "story": self.setup_state.get("story"),
        })
        await self._send("phase", phase="analyze", status="done")

        homework_path = str(self.session_dir / "homework.md") if result.get("homework") else None
        await self._send("done", transcript_path=str(transcript_path), homework_path=homework_path)
