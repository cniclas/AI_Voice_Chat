"""Standalone Piper TTS server to run on a remote host, paired with the
app's "tts": {"backend": "remote"} setting.

Piper is CPU-fast — a GPU is not required — so this runs fine on the same
GPU box that serves the LLM, a cheap VPS, or a HuggingFace Inference
Endpoint custom container (point the endpoint at this app, port 8080).

Setup on the remote host (this file is self-contained — no other repo code
is needed):

    pip install piper-tts fastapi uvicorn
    mkdir voices   # copy the same .onnx + .onnx.json files from piper/voices/
    PIPER_SERVER_TOKEN=<shared-secret> python piper_server.py

Then in backends.json on the machine running the app:

    "tts": {"backend": "remote", "url": "http://<host>:8080/tts",
            "api_key": "<shared-secret>"}

API: POST /tts with JSON {"text": "...", "lang": "en"|"es"} returns
audio/wav. GET /health lists the loaded voices.
"""

import io
import os
import wave

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from piper.config import SynthesisConfig
from piper.voice import PiperVoice

VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")

VOICE_FILES = {
    "en": "en_US-lessac-medium.onnx",
    "es": "es_MX-claude-high.onnx",
}

# Keep in sync with piper/tts.py so local and remote synthesis sound the same.
SPEED_SCALE = 1.08

# Optional bearer-token auth; unset = open (only do that on a private network).
TOKEN = os.environ.get("PIPER_SERVER_TOKEN", "")

voices = {
    lang: PiperVoice.load(os.path.join(VOICES_DIR, filename))
    for lang, filename in VOICE_FILES.items()
    if os.path.exists(os.path.join(VOICES_DIR, filename))
}

app = FastAPI()


class TTSRequest(BaseModel):
    text: str
    lang: str = "es"


@app.get("/health")
def health():
    return {"ok": True, "voices": sorted(voices)}


@app.post("/tts")
def tts(req: TTSRequest, authorization: str | None = Header(default=None)):
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="bad or missing bearer token")
    if req.lang not in voices:
        raise HTTPException(status_code=400, detail=f"unsupported lang {req.lang!r}; loaded: {sorted(voices)}")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voices[req.lang].synthesize_wav(
            req.text, wav_file, syn_config=SynthesisConfig(length_scale=SPEED_SCALE))
    return Response(content=buf.getvalue(), media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
