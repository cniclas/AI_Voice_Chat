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
# kokoro/whisper package directories, same layout main.py relies on.
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "whisper"))
sys.path.insert(0, os.path.join(_ROOT, "kokoro"))

import whisper
from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from web.session import SessionOrchestrator
from session_core import RECORDINGS_ROOT

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Loading Whisper model...")
    app.state.whisper_model = whisper.load_model("large-v3")
    app.state.whisper_lock = asyncio.Lock()
    print("Whisper ready.")
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
    await ws.accept()
    orchestrator = SessionOrchestrator(ws, app.state.whisper_model, app.state.whisper_lock)
    await orchestrator.run()
