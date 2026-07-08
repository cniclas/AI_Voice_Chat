"""Selectable model backends for the three model services.

Each service can run locally (the default — identical to the app's original
behavior) or against a remote host:

- ``llm``  — "ollama" (local or remote Ollama) or "openai" (any
  OpenAI-compatible /v1/chat/completions endpoint: HuggingFace Inference
  Endpoints/router, TGI, vLLM, ...).
- ``stt``  — "local" (the bundled Whisper package) or "remote" (an HTTP
  endpoint that accepts a POSTed WAV body and returns {"text": ...}, e.g. a
  HuggingFace automatic-speech-recognition endpoint).
- ``tts``  — "local" (the bundled Piper voices) or "remote" (an HTTP endpoint
  that accepts {"text", "lang"} JSON and returns WAV bytes — see
  remote/piper_server.py for a ready-made server).

Configuration lives in ``backends.json`` at the repo root (see
``backends.example.json``). The file is gitignored because it may hold API
keys. A missing file, missing service, or missing key falls back to the
local defaults below, so a fresh clone behaves exactly as before.
"""

import json
import os
from pathlib import Path

_ROOT = Path(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = _ROOT / "backends.json"

DEFAULTS = {
    "llm": {
        "backend": "ollama",        # "ollama" | "openai"
        "base_url": "http://localhost:11434",
        "model": "llama3.1:8b",
        "api_key": "",
    },
    "stt": {
        "backend": "local",         # "local" | "remote"
        "whisper_model": "large-v3",
        "url": "",
        "api_key": "",
    },
    "tts": {
        "backend": "local",         # "local" | "remote"
        "url": "",
        "api_key": "",
    },
}


def _load() -> dict:
    config = {service: dict(defaults) for service, defaults in DEFAULTS.items()}
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: {CONFIG_PATH.name} is invalid ({e}); using local defaults.")
            return config
        for service, overrides in user.items():
            if service in config and isinstance(overrides, dict):
                config[service].update(overrides)
    return config


_CONFIG = _load()


def get(service: str) -> dict:
    """Return the effective config dict for "llm", "stt", or "tts"."""
    return _CONFIG[service]
