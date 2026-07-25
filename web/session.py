"""SessionOrchestrator: drives one tutoring session over a single WebSocket
connection.

Unlike the terminal UI (`main.py`), which runs the five-phase arc top to
bottom, the browser session is *command-driven*. On connect the orchestrator
only signals readiness; nothing happens until the user picks an entry point:

- ``start_talk``      — a plain push-to-talk conversation, no story preamble.
- ``start_challenge`` — today's translation challenge.

The translation challenge is the browser's replacement for the old "Wikipedia
story" entry point, and it runs the written-practice loop end to end:

1. today's Wikipedia "on this day" article is picked as before, but instead of
   a story to chat about it becomes a full graded lesson — story, plus the
   sentence-by-sentence literal and natural translation that serves as the
   answer key (`translation_challenge.build_lesson`, the local-model port of
   the `language-lesson` skill);
2. the Spanish story is read aloud once, exactly like the old story mode;
3. the student translates it back into English out loud, and that turn is
   marked against the answer key (`translation_challenge.review_translation`,
   the port of the `translation-review` skill);
4. push-to-talk then continues normally, with the story and the review in the
   tutor's system prompt so follow-up questions can be answered from the text
   the student just worked through;
5. Exit ends the session, and the analyze/homework phase records both the
   conversation's weaknesses and the translation's in the student profile.

Status updates are streamed to the client throughout so the user always sees
what the backend is doing (transcribing, glossing, marking, …).
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import requests
from fastapi import WebSocket, WebSocketDisconnect

import curriculum
import translation_challenge
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
    build_challenge_system_prompt,
)

_PROMPT_TO_SPEAK = "Your turn — press a language button and speak."
_PROMPT_TO_TRANSLATE = (
    "Your turn to translate — press 🎙 English and say what the story means, "
    "sentence by sentence."
)

LESSON_FILENAME = "lesson.md"
REVIEW_FILENAME = "translation_review.md"
REVIEW_JSON_FILENAME = "translation_review.json"


class _SessionEnded(Exception):
    """Raised when the client explicitly quits before a phase completes."""


class SessionOrchestrator:
    def __init__(self, ws: WebSocket, app_state, whisper_lock: asyncio.Lock, user):
        self.ws = ws
        self.app_state = app_state
        self.whisper_model = None
        self.whisper_lock = whisper_lock
        self.user = user
        self.session_dir: Path | None = None
        self.session_name: str | None = None
        self.profile: dict | None = None
        self.llm_history: list = []
        self.responses: list[Response] = []
        self.setup_state: dict = {}
        self.lesson: dict | None = None
        self.review: dict | None = None
        self.mode: str | None = None  # "talk" or "challenge" once a session starts
        # "translate" while the challenge is waiting for the spoken
        # translation, "converse" for ordinary push-to-talk turns.
        self.stage: str = "converse"
        self.awaiting_mode = True

    async def _send(self, type_: str, **payload):
        await self.ws.send_json({"type": type_, **payload})

    async def _status(self, state: str, message: str):
        """Push a combined avatar-state + human-readable status line."""
        await self._send("status", state=state, message=message)

    async def _set_stage(self, stage: str, prompt: str):
        """Tell the client which turn it is: translating the story, or talking
        freely. The client uses this to steer the language buttons — the
        translation has to be in English — and to keep the right idle prompt on
        screen after audio finishes playing."""
        self.stage = stage
        await self._send("stage", stage=stage, prompt=prompt)

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
            elif msg_type in {"start_talk", "start_challenge"}:
                if not self.awaiting_mode:
                    continue
                self.awaiting_mode = False
                if msg_type == "start_talk":
                    await self._start_talk()
                else:
                    await self._start_challenge()
            elif msg_type == "user_audio":
                if self.mode is None:
                    # The header is always followed by one binary frame;
                    # consume it to stay in sync.
                    await self.ws.receive_bytes()
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
            self.session_dir = create_session_dir(self.user.recordings_dir)
            self.session_name = f"{self.user.id}/{self.session_dir.name}"

    def _progress_reporter(self):
        """A thread-safe `progress(done, total, label)` for the lesson build,
        which runs in a worker thread but wants to push status lines onto the
        socket owned by this event loop."""
        loop = asyncio.get_running_loop()

        def report(done: int, total: int, label: str):
            # The step count is unknown until the story has been written and
            # split into sentences, so it only joins the line once it means
            # something.
            counter = f" ({done}/{total})" if total else ""
            future = asyncio.run_coroutine_threadsafe(
                self._status("thinking", f"Building today's challenge — {label}…{counter}"),
                loop,
            )
            # Retrieve any exception (a closed socket, typically) so it doesn't
            # surface later as an un-awaited-future warning.
            future.add_done_callback(lambda f: f.cancelled() or f.exception())

        return report

    # -- Entry points --------------------------------------------------------

    async def _start_talk(self):
        """Free conversation: no story, base tutoring prompt."""
        if self.mode is not None:
            return
        self.mode = "talk"
        self._ensure_session_dir()
        if self.profile is None:
            self.profile = await asyncio.to_thread(curriculum.load_profile, self.user.profile_path)
        self.llm_history = [{"role": "system", "content": build_system_prompt(None)}]
        await self._send("mode", mode="talk", session_name=self.session_name)
        await self._status("idle", _PROMPT_TO_SPEAK)

    async def _start_challenge(self):
        """Today's translation challenge: build the lesson from Wikipedia,
        narrate the Spanish story, then wait for the spoken translation."""
        if self.mode is not None:
            return
        self.mode = "challenge"
        self._ensure_session_dir()
        await self._send("mode", mode="challenge", session_name=self.session_name)

        await self._status(
            "thinking",
            "Fetching today's Wikipedia article and building your translation challenge…")
        self.setup_state = await asyncio.to_thread(
            session_setup_graph.invoke,
            {"session_dir": str(self.session_dir), "mode": "challenge", "user_id": self.user.id},
            {"configurable": {"progress": self._progress_reporter()}},
        )
        self.profile = self.setup_state["profile"]
        self.lesson = self.setup_state.get("lesson")

        if self.setup_state.get("setup_failed") or not self.lesson:
            # Wikipedia or Ollama let us down: degrade to a plain conversation
            # rather than leaving the user on a dead screen.
            await self._send("error", message=self.setup_state.get(
                "setup_failed", "Today's challenge could not be built."))
            self.llm_history = [{"role": "system", "content": build_system_prompt(None)}]
            await self._set_stage("converse", _PROMPT_TO_SPEAK)
            await self._status("idle", _PROMPT_TO_SPEAK)
            return

        # The tutor can already answer questions about the story; the review is
        # folded into the prompt once it exists.
        self.llm_history = [
            {"role": "system", "content": build_challenge_system_prompt(self.lesson, None)}]

        await self._status("speaking", "Reading today's story aloud…")
        await asyncio.to_thread(
            synthesize,
            f"{self.lesson['title']}. {self.lesson['story']}",
            lang="es",
            output_file=str(self.session_dir / "story_es.wav"),
            play=False,
        )
        # The story arrives as a normal assistant chat bubble, so it gets the
        # same audio control as every other reply. Sent only after synthesize()
        # has written story_es.wav: the bubble's audio element is the single
        # playback source, and it fetches that file from the session route.
        # tts_audio is just the auto-play cue.
        await self._send(
            "transcript",
            author="assistant",
            language="es",
            text=f"{self.lesson['title']}\n\n{self.lesson['story']}",
            audio_filename="story_es.wav",
        )
        await self._send("tts_audio", turn="story")
        await self._wait_for("tts_playback_done")

        # lesson.md carries the answer key, so it is deliberately not linked
        # until the translation has been marked.
        await self._set_stage("translate", _PROMPT_TO_TRANSLATE)
        await self._status("idle", _PROMPT_TO_TRANSLATE)

    # -- Per-turn conversation ----------------------------------------------

    async def _handle_turn(self, header: dict):
        # A user_audio header is always followed by one binary frame.
        language = header.get("language", "en")
        audio_bytes = await self.ws.receive_bytes()

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
            await self._status(
                "idle",
                "No speech detected — try again."
                if self.stage != "translate" else
                f"No speech detected. {_PROMPT_TO_TRANSLATE}")
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

        if self.stage == "translate":
            if language == "en":
                await self._mark_translation(user_text)
                return
            # Answering in Spanish means the student has moved on from the
            # translation; take them at their word and just talk.
            await self._set_stage("converse", _PROMPT_TO_SPEAK)

        await self._reply_to(user_text, language)

    async def _reply_to(self, user_text: str, language: str):
        """One ordinary tutoring turn: LLM reply, spoken back."""
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

    async def _mark_translation(self, translation_text: str):
        """Mark the spoken translation against the lesson's answer key, then
        hand the session over to ordinary conversation."""
        await self._status("thinking", "Marking your translation against the story…")
        try:
            self.review = await asyncio.to_thread(
                translation_challenge.review_translation, self.lesson, translation_text)
        except (requests.RequestException, ValueError, KeyError) as e:
            await self._send("error", message=f"Could not mark the translation: {e}")
            await self._set_stage("converse", _PROMPT_TO_SPEAK)
            await self._status("idle", _PROMPT_TO_SPEAK)
            return

        review_markdown = translation_challenge.render_review_markdown(self.review, self.lesson)
        (self.session_dir / REVIEW_FILENAME).write_text(review_markdown, encoding="utf-8")
        (self.session_dir / REVIEW_JSON_FILENAME).write_text(
            json.dumps(self.review, indent=2, ensure_ascii=False), encoding="utf-8")

        # The bubble carries the full review; the spoken version is a short
        # summary, because a list of sentence-by-sentence corrections is much
        # easier to read than to listen to.
        chat_text = translation_challenge.review_chat_text(self.review)
        spoken = translation_challenge.spoken_review_summary(self.review)

        await self._status("thinking", "Synthesizing speech…")
        assistant_audio_path = generate_audio_filename(self.session_dir, "assistant", "en")
        await asyncio.to_thread(
            synthesize, spoken, lang="en", output_file=assistant_audio_path, play=False)
        self.responses.append(Response(
            author="assistant", language="en", text=chat_text,
            timestamp=datetime.now(), audio_sample=assistant_audio_path,
        ))

        # The tutor now answers questions with the findings in hand.
        self.llm_history = [
            {"role": "system", "content": build_challenge_system_prompt(self.lesson, self.review)}]

        # Stage first, so the client already knows the right idle prompt by the
        # time the review audio finishes playing.
        await self._set_stage("converse", _PROMPT_TO_SPEAK)
        await self._send(
            "transcript",
            author="assistant",
            language="en",
            text=chat_text,
            audio_filename=Path(assistant_audio_path).name,
        )
        await self._send("tts_audio", turn="reply")
        # Now that the translation is marked, the answer key is safe to open.
        await self._send("artifact", filename=LESSON_FILENAME,
                         label="Open the lesson (story + literal translation)")
        await self._send("artifact", filename=REVIEW_FILENAME,
                         label="Open the full translation review")

    # -- Wrap-up -------------------------------------------------------------

    def _artifact_names(self) -> dict:
        """Filenames the completion screen can link to, for the ones that
        actually got written."""
        names = {}
        for key, filename in (("lesson_filename", LESSON_FILENAME),
                              ("review_filename", REVIEW_FILENAME)):
            if self.session_dir and (self.session_dir / filename).is_file():
                names[key] = filename
        return names

    async def _analyze_and_persist(self):
        if self.session_dir is None:
            try:
                await self._send("done", transcript_filename=None, homework_filename=None)
            except (WebSocketDisconnect, RuntimeError):
                pass
            return

        if not self.responses:
            # Nothing was spoken. A challenge that got as far as building a
            # lesson still counts as practice, so record it — but there is no
            # transcript to analyze and no homework to write.
            if self.lesson:
                await asyncio.to_thread(self._record_lesson_only_practice)
            try:
                await self._send("done", session_name=self.session_name,
                                 transcript_filename=None, homework_filename=None,
                                 **self._artifact_names())
            except (WebSocketDisconnect, RuntimeError):
                pass
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
            "mode": self.mode or "talk",
            "profile": self.profile,
            "transcript_text": transcript_text,
            "story": self.setup_state.get("story"),
            "lesson": self.lesson,
            "review": self.review,
            "spoken_turns": sum(1 for r in self.responses if r.author == "user"),
            "user_id": self.user.id,
        })

        homework_path = str(self.session_dir / "homework.md") if result.get("homework") else None
        try:
            await self._send(
                "done",
                session_name=self.session_name,
                transcript_filename=transcript_path.name,
                homework_filename=Path(homework_path).name if homework_path else None,
                **self._artifact_names(),
            )
        except (WebSocketDisconnect, RuntimeError):
            pass

    def _record_lesson_only_practice(self):
        """Track record for a challenge the student left before speaking. Runs
        off the event loop; the article itself was already recorded eagerly by
        the setup graph."""
        profile = self.profile or curriculum.load_profile(self.user.profile_path)
        curriculum.record_practice(
            profile,
            session_name=self.session_dir.name,
            mode=self.mode or "challenge",
            focus=self.lesson.get("focus"),
            level=self.lesson.get("level"),
            topic=self.lesson.get("title"),
            translated=False,
            spoken_turns=0,
        )
        curriculum.save_profile(profile, self.user.profile_path)
