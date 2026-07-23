"""UI-design playground entry point. Serves the exact same frontend as
web.server:app, but every button press plays the predefined manuscript in
web/demo_manuscript.json instead of running Whisper/Ollama/Kokoro/Wikipedia.
Starts instantly — no models are imported or loaded. Run with:

    uv run python -m uvicorn web.demo:app --host 127.0.0.1 --port 8000 --reload
"""

from web.app_factory import create_app

app = create_app(demo=True)
