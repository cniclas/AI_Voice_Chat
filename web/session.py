"""Mode-based session orchestrators, one per conversation mode, all driving a
single WebSocket connection:

- ``StorySession``      — the original Wikipedia-story arc (prepare/narrate/
  converse/analyze/homework).
- ``HomeworkSession``   — voice conversation steered by the latest saved
  homework (or a profile-derived lesson); the tutor drives it exercise by
  exercise.
- ``FlashcardSession``  — voice-input-only: the student says the Spanish word
  for each English prompt; an LLM judge grades it (synonyms accepted). No
  spoken LLM replies.
- ``FillBlankSession``  — same shape, but the student says the missing word
  of a cloze sentence.

Quit semantics: the client may send ``abort_session`` at any time (quit or
mode switch). An aborted session leaves NO artifacts — the whole session
folder is deleted. Only a finished session persists anything: chat modes on
``end_session`` (transcript + analysis + homework), exercise modes on
completing the deck (results.md + profile vocab bump).
"""

import asyncio
import json
import shutil
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
import curriculum
import exercises


class _SessionEnded(Exception):
    """Client pressed Finish: wrap up and persist what the mode allows."""


class _SessionAborted(Exception):
    """Client quit or switched mode: discard every session artifact."""


class BaseSession:
    """Shared WebSocket plumbing: message routing, user-turn capture
    (audio → Whisper transcript), TTS delivery, and discard-on-abort."""

    mode = "base"

    def __init__(self, ws: WebSocket, whisper_model, whisper_lock: asyncio.Lock):
        self.ws = ws
        self.whisper_model = whisper_model
        self.whisper_lock = whisper_lock
        self.session_dir: Path | None = None
        self.profile: dict | None = None
        self.responses: list[Response] = []
        self.completed = False

    async def _send(self, type_: str, **payload):
        await self.ws.send_json({"type": type_, **payload})

    async def run(self):
        self.session_dir = create_session_dir()
        try:
            await self._send("mode_started", mode=self.mode)
            await self._run_mode()
            self.completed = True
        except _SessionEnded:
            self.completed = await self._finish_session()
        except _SessionAborted:
            try:
                await self._send("aborted")
            except Exception:
                pass
        except WebSocketDisconnect:
            pass
        finally:
            if not self.completed and self.session_dir is not None:
                await asyncio.to_thread(shutil.rmtree, self.session_dir, ignore_errors=True)

    async def _run_mode(self):
        raise NotImplementedError

    async def _finish_session(self) -> bool:
        """Handle an early Finish. Return True if artifacts were persisted
        (keep the session folder), False to discard it. Exercise modes only
        persist on full completion, so an early finish is a discard."""
        return False

    async def _recv_json(self) -> dict:
        """Receive the next JSON control message, translating quit signals
        into exceptions and skipping unexpected binary frames."""
        while True:
            message = await self.ws.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code") or 1000)
            text = message.get("text")
            if text is None:
                continue  # stray binary frame — drop it
            msg = json.loads(text)
            msg_type = msg.get("type")
            if msg_type == "abort_session":
                raise _SessionAborted()
            if msg_type == "end_session":
                raise _SessionEnded()
            return msg

    async def _wait_for(self, expected_type: str) -> dict:
        while True:
            msg = await self._recv_json()
            if msg.get("type") == expected_type:
                return msg

    async def _receive_user_turn(self, forced_language: str | None = None):
        """Wait for a user_audio turn, save + transcribe it. Returns
        (language, text, audio_path); loops on empty transcriptions."""
        while True:
            header = await self._recv_json()
            if header.get("type") != "user_audio":
                continue
            language = forced_language or header.get("language", "es")
            audio_bytes = await self.ws.receive_bytes()
            audio_path = generate_audio_filename(self.session_dir, "user", language)
            Path(audio_path).write_bytes(audio_bytes)

            async with self.whisper_lock:
                text = await asyncio.to_thread(
                    transcribe_audio, audio_path, self.whisper_model, language)
            if not text:
                await self._send("no_speech")
                continue
            return language, text, audio_path

    async def _speak(self, text: str, language: str, output_file: str, turn: str = "reply"):
        """Synthesize `text` and stream the WAV to the client."""
        audio_bytes = await asyncio.to_thread(
            synthesize, text, lang=language, output_file=output_file, play=False)
        await self._send("tts_audio", turn=turn)
        await self.ws.send_bytes(audio_bytes)


# ---------------------------------------------------------------------------
# Chat modes (story / homework): free voice conversation + analysis on finish
# ---------------------------------------------------------------------------

class ChatSessionBase(BaseSession):
    """Free conversation loop; on Finish, saves the transcript and runs the
    analysis graph (weaknesses → homework → profile update)."""

    story_for_analysis: dict | None = None  # passed to the analysis graph
    llm_history: list

    async def _run_mode(self):
        await self._prepare()
        await self._send("phase", phase="converse", status="running")
        while True:  # exits only via _SessionEnded/_SessionAborted from _recv_json
            language, user_text, audio_path = await self._receive_user_turn()
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
            await self._speak(response_text, language, assistant_audio_path)

    async def _prepare(self):
        """Set self.profile and self.llm_history; send any mode intro."""
        raise NotImplementedError

    async def _finish_session(self) -> bool:
        if not self.responses:
            await self._send("done", transcript_path=None, homework_path=None)
            return False  # nothing worth keeping — discard the folder

        await self._send("phase", phase="analyze", status="running")
        transcript_path = save_transcript(self.responses, self.session_dir)
        transcript_text = format_transcript_for_lesson(self.responses)

        result = await asyncio.to_thread(session_analysis_graph.invoke, {
            "session_dir": str(self.session_dir),
            "profile": self.profile,
            "transcript_text": transcript_text,
            "story": self.story_for_analysis,
        })
        await self._send("phase", phase="analyze", status="done")

        homework_path = str(self.session_dir / "homework.md") if result.get("homework") else None
        await self._send("done", transcript_path=str(transcript_path), homework_path=homework_path)
        return True


class StorySession(ChatSessionBase):
    """Wikipedia-story mode: prepare today's story, narrate it, then chat."""

    mode = "story"

    async def _prepare(self):
        await self._send("phase", phase="prepare", status="running")
        setup_state = await asyncio.to_thread(
            session_setup_graph.invoke, {"session_dir": str(self.session_dir)})
        self.profile = setup_state["profile"]
        self.story_for_analysis = setup_state.get("story")

        if setup_state.get("setup_failed"):
            await self._send("error", message=setup_state["setup_failed"])
        daily = daily_story_from_setup_state(setup_state)

        await self._send(
            "story",
            article_title=daily["article_title"] if daily else None,
            story_title=daily["story_title"] if daily else None,
            story=daily["story"] if daily else None,
        )
        await self._send("phase", phase="prepare", status="done")

        if daily:
            await self._send("phase", phase="narrate", status="running")
            await self._speak(
                f"{daily['story_title']}. {daily['story']}", "es",
                str(self.session_dir / "story_es.wav"), turn="story")
            await self._wait_for("tts_playback_done")
            await self._send("phase", phase="narrate", status="done")

        self.llm_history = [{"role": "system", "content": build_system_prompt(daily)}]


class HomeworkSession(ChatSessionBase):
    """Homework mode: the tutor drives the latest saved homework (or a
    profile-derived lesson) exercise by exercise, starting the conversation
    itself."""

    mode = "homework"

    async def _prepare(self):
        await self._send("phase", phase="prepare", status="running")
        self.profile = await asyncio.to_thread(curriculum.load_profile)

        lesson = await asyncio.to_thread(exercises.find_latest_homework)
        if lesson is None:
            lesson = exercises.lesson_from_profile(self.profile)
        await self._send("lesson", title="Today's lesson", body=lesson)
        await self._send("phase", phase="prepare", status="done")

        self.llm_history = [{"role": "system", "content": exercises.build_homework_system_prompt(lesson)}]

        try:
            opening = await asyncio.to_thread(exercises.homework_opening_turn, self.llm_history)
        except requests.RequestException as e:
            await self._send("error", message=f"Ollama error: {e}. Make sure Ollama is running (ollama serve).")
            return  # the converse loop still works once Ollama is back

        assistant_audio_path = generate_audio_filename(self.session_dir, "assistant", "es")
        self.responses.append(Response(
            author="assistant", language="es", text=opening,
            timestamp=datetime.now(), audio_sample=assistant_audio_path,
        ))
        await self._send("transcript", author="assistant", language="es", text=opening)
        await self._speak(opening, "es", assistant_audio_path)


# ---------------------------------------------------------------------------
# Exercise modes (flashcards / fill-in-the-blanks): card → answer → judgement
# ---------------------------------------------------------------------------

class ExerciseSessionBase(BaseSession):
    """Card-based drill: the student answers each card by voice, an LLM judge
    grades it, and nothing is spoken back. Artifacts (results.md + profile
    update) are only persisted when the whole deck is completed."""

    title = "Exercise"
    answer_language = "es"

    def _build_items(self, profile: dict) -> list[dict]:
        raise NotImplementedError

    def _card_payload(self, item: dict) -> dict:
        raise NotImplementedError

    def _judge(self, item: dict, heard: str) -> dict:
        raise NotImplementedError

    async def _run_mode(self):
        await self._send("phase", phase="prepare", status="running")
        self.profile = await asyncio.to_thread(curriculum.load_profile)
        try:
            items = await asyncio.to_thread(self._build_items, self.profile)
        except (requests.RequestException, ValueError) as e:
            await self._send("error", message=f"Could not prepare the exercises: {e}. Make sure Ollama is running (ollama serve).")
            raise _SessionAborted()
        await self._send("phase", phase="prepare", status="done")
        await self._send("phase", phase="exercise", status="running")

        results = []
        for i, item in enumerate(items):
            await self._send("card", index=i, total=len(items), **self._card_payload(item))
            language, heard, audio_path = await self._receive_user_turn(
                forced_language=self.answer_language)
            self.responses.append(Response(
                author="user", language=language, text=heard,
                timestamp=datetime.now(), audio_sample=audio_path,
            ))
            verdict = await asyncio.to_thread(self._judge, item, heard)
            results.append({"item": item, "heard": heard,
                            "correct": verdict["correct"],
                            "feedback": verdict.get("feedback", "")})
            await self._send(
                "judgement", index=i, total=len(items),
                correct=verdict["correct"], expected=item["answer"],
                heard=heard, feedback=verdict.get("feedback", ""))
            if i < len(items) - 1:
                await self._wait_for("next_card")

        n_correct = sum(1 for r in results if r["correct"])
        results_path = await asyncio.to_thread(
            exercises.save_exercise_results, self.session_dir, self.title, results)
        await asyncio.to_thread(self._update_profile, results)
        await self._send("exercise_summary", correct=n_correct, total=len(results))
        await self._send("done", results_path=str(results_path))

    def _update_profile(self, results: list[dict]):
        """Mark correctly answered practice words as targeted today."""
        correct_words = [r["item"]["answer"] for r in results if r["correct"]]
        curriculum.bump_vocab_targeted(self.profile, correct_words)
        curriculum.save_profile(self.profile)


class FlashcardSession(ExerciseSessionBase):
    mode = "flashcards"
    title = "Flashcards"

    def _build_items(self, profile):
        return exercises.generate_flashcards(profile)

    def _card_payload(self, item):
        return {"prompt": item["prompt"]}

    def _judge(self, item, heard):
        return exercises.judge_flashcard(item["prompt"], item["answer"], heard)


class FillBlankSession(ExerciseSessionBase):
    mode = "fill_blanks"
    title = "Fill in the blanks"

    def _build_items(self, profile):
        return exercises.generate_fill_blanks(profile)

    def _card_payload(self, item):
        return {"sentence": item["sentence"], "hint": item.get("hint", "")}

    def _judge(self, item, heard):
        return exercises.judge_fill_blank(
            item["sentence"], item["answer"], item.get("hint", ""), heard)


MODE_SESSIONS = {
    cls.mode: cls
    for cls in (StorySession, HomeworkSession, FlashcardSession, FillBlankSession)
}
