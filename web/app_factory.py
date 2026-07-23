"""App factory for the browser UI: static frontend + a single WebSocket
endpoint per tutoring session. This module deliberately builds no app at
import time — web/server.py instantiates the real app (Whisper + Kokoro) and
web/demo.py the demo app (manuscript replay, no heavy imports); importing the
factory itself stays lightweight for both."""

import sys
import os
import glob
import site
import asyncio
from contextlib import asynccontextmanager, suppress
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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from session_core import RECORDINGS_ROOT

STATIC_DIR = Path(__file__).parent / "static"


def create_app(demo: bool = False) -> FastAPI:
    """Build the app. With ``demo=True`` the heavy pipeline is never imported:
    no Whisper load, no Kokoro pipelines, and every WebSocket session is
    driven by the predefined manuscript in web/demo_manuscript.json."""
    if demo:
        from web.demo_session import DemoSessionOrchestrator as Orchestrator
    else:
        # Imported lazily so demo mode starts instantly (importing whisper and
        # web.session pulls in torch and the Kokoro TTS pipelines).
        import whisper
        from web.session import SessionOrchestrator as Orchestrator

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start Whisper loading in the background so the web UI can be served
        # immediately and show a "Loading" status while the model initializes.
        app.state.whisper_model = None
        app.state.whisper_ready = asyncio.Event()
        app.state.whisper_lock = asyncio.Lock()

        if demo:
            # Nothing to load — sessions are ready the moment a client connects.
            app.state.whisper_ready.set()
        else:
            async def _load_whisper():
                try:
                    print("Loading Whisper model in background...")
                    model = await asyncio.to_thread(whisper.load_model, "large-v3")
                    app.state.whisper_model = model
                    app.state.whisper_ready.set()
                    print("Whisper ready.")
                except Exception as e:
                    print(f"Whisper load failed: {e}")

            # Fire-and-forget background loading task.
            asyncio.create_task(_load_whisper())
        try:
            yield
        finally:
            # Nothing special to clean up for Whisper; keep consistent state.
            pass

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def no_store(request, call_next):
        # Frontend files and session artifacts change constantly while the
        # UI is being iterated on (and demo mode rewrites recordings/demo per
        # connection). Never let the browser cache them: a stale app.js
        # against a newer WebSocket protocol breaks the page silently.
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

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
        # Accept the websocket and run the session orchestrator. Any unexpected
        # exception should be caught and reported back to the client instead of
        # letting the socket drop silently.
        await ws.accept()
        orchestrator = Orchestrator(ws, app.state, app.state.whisper_lock)
        try:
            await orchestrator.run()
        except WebSocketDisconnect:
            # Normal client disconnect; nothing to do.
            raise
        except Exception as e:
            # Try to tell the client what happened, then close.
            try:
                await ws.send_json({"type": "error", "message": f"Server error: {e}"})
            except Exception:
                pass
            raise

    return app
