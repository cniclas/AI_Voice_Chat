"""Real-pipeline entry point (Whisper + Ollama + Kokoro). Run with:

    uv run python -m uvicorn web.server:app --host 127.0.0.1 --port 8000 --reload

For the UI-design playground that replays a recorded session instead, run
`web.demo:app` — see web/demo.py.
"""

from web.app_factory import create_app

app = create_app()
