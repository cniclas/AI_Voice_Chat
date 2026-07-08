"""FastAPI app serving the browser UI: static frontend + a single WebSocket
endpoint per tutoring session. Run with:

    uv run python -m uvicorn web.server:app --host 127.0.0.1 --port 8000
"""

import sys
import os
import glob
import site
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

for _p in glob.glob(os.path.join(_ROOT, ".venv", "Lib", "site-packages")):
    site.addsitedir(_p)

# Repo root (for curriculum/session_core/session_graphs) and the local
# piper/whisper package directories, same layout main.py relies on.
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "whisper"))
sys.path.insert(0, os.path.join(_ROOT, "piper"))

import webbrowser

from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.websockets import WebSocketDisconnect

import backends
from web.session import MODE_SESSIONS
from session_core import RECORDINGS_ROOT

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    stt = backends.get("stt")
    if stt["backend"] == "local":
        print("Loading Whisper model...")
        import whisper  # deferred: pulls in torch, pointless for remote STT
        app.state.whisper_model = whisper.load_model(stt["whisper_model"])
        print("Whisper ready.")
    else:
        app.state.whisper_model = None
        print(f"Using remote speech-to-text at {stt['url']}")
    app.state.whisper_lock = asyncio.Lock()
    # Set by run.py so `python run.py` opens the UI once the server is usable.
    if os.environ.get("AI_TUTOR_OPEN_BROWSER") == "1":
        webbrowser.open(os.environ.get("AI_TUTOR_URL", "http://127.0.0.1:8000"))
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/session/{session_name}/{filename}")
async def session_file(session_name: str, filename: str):
    """Serve a generated session artifact (transcript.md, homework.md, ...)."""
    recordings_root = Path(RECORDINGS_ROOT).resolve()
    path = (recordings_root / session_name / filename).resolve()
    if recordings_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(path))


@app.websocket("/ws/session")
async def ws_session(ws: WebSocket):
    """One session per connection. The first client message picks the mode:
    {"type": "start", "mode": "story"|"homework"|"flashcards"|"fill_blanks"}."""
    await ws.accept()
    try:
        start = await ws.receive_json()
    except WebSocketDisconnect:
        return
    session_cls = MODE_SESSIONS.get(start.get("mode")) if start.get("type") == "start" else None
    if session_cls is None:
        await ws.send_json({"type": "error", "message": f"Unknown mode: {start.get('mode')!r}"})
        await ws.close()
        return
    orchestrator = session_cls(ws, app.state.whisper_model, app.state.whisper_lock)
    await orchestrator.run()
