"""Text-to-speech via the configured TTS backend (backends.json):

- local  — the bundled Piper voices in piper/voices/, loaded lazily on the
  first synthesis call per language (so the app can start without local
  voice files when the remote backend is configured).
- remote — an HTTP endpoint that accepts {"text", "lang"} JSON and returns
  WAV bytes; see remote/piper_server.py for a ready-made server.
"""

import io
import os
import sys
import wave
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd

_PIPER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_PIPER_DIR))  # repo root, for `backends`

import backends

_VOICES_DIR = os.path.join(_PIPER_DIR, "voices")

_VOICE_FILES = {
    "en": "en_US-lessac-medium.onnx",
    "es": "es_MX-claude-high.onnx",
}

# Make the synthesized voice slightly slower and more natural for speech playback.
_SPEED_SCALE = 1.08

_voices: dict = {}


def _get_voice(lang: str):
    """Load a local Piper voice on first use and cache it."""
    if lang not in _voices:
        from piper.voice import PiperVoice
        _voices[lang] = PiperVoice.load(os.path.join(_VOICES_DIR, _VOICE_FILES[lang]))
    return _voices[lang]


def _output_samplerate(default: int = 44100) -> int:
    """Native sample rate of the default output device."""
    try:
        return int(round(sd.query_devices(kind="output")["default_samplerate"]))
    except Exception:
        return default


# Pre-resample to the device's native rate to avoid quality loss from the OS
# resampler.
_DEVICE_RATE = _output_samplerate()

# Piper can sound clipped at the very end of a sentence when the model stops
# abruptly. A tiny tail of silence makes the final word feel complete.
_TAIL_SECONDS = 0.2


def _resample_to(audio_i16: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Band-limited (Fourier) resample of int16 audio to dst_rate."""
    if src_rate == dst_rate:
        return audio_i16
    x = audio_i16.astype(np.float64)
    n = len(x)
    m = int(round(n * dst_rate / src_rate))
    spectrum = np.fft.rfft(x)
    out = np.zeros(m // 2 + 1, dtype=complex)
    k = min(len(spectrum), len(out))
    out[:k] = spectrum[:k]
    y = np.fft.irfft(out, m) * (m / n)
    return np.clip(np.round(y), -32768, 32767).astype(np.int16)


def _synthesize_local(text: str, lang: str) -> bytes:
    from piper.config import SynthesisConfig
    voice = _get_voice(lang)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file, syn_config=SynthesisConfig(length_scale=_SPEED_SCALE))
    return buf.getvalue()


def _synthesize_remote(text: str, lang: str, cfg: dict) -> bytes:
    headers = {}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    response = requests.post(cfg["url"], json={"text": text, "lang": lang},
                             headers=headers, timeout=120)
    response.raise_for_status()
    return response.content


def _play_wav_bytes(wav_bytes: bytes) -> None:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    audio_data = np.frombuffer(frames, dtype=np.int16)

    play_data = _resample_to(audio_data, sample_rate, _DEVICE_RATE)
    if _TAIL_SECONDS > 0:
        tail = np.zeros(int(_DEVICE_RATE * _TAIL_SECONDS), dtype=np.int16)
        play_data = np.concatenate([play_data, tail])

    sd.play(play_data, samplerate=_DEVICE_RATE, blocking=True)


def synthesize(text: str, lang: str, output_file: str | None = None, play: bool = True) -> bytes | None:
    """
    Synthesize text using the given language ("en" or "es").
    Saves to a WAV file and/or plays the audio locally.

    When play=False, returns the synthesized WAV bytes instead of playing
    them locally — used by the browser UI, which owns audio playback
    client-side (so the user can route TTS output to a different device
    than the one used for microphone capture).
    """
    cfg = backends.get("tts")
    if cfg["backend"] == "remote":
        wav_bytes = _synthesize_remote(text, lang, cfg)
    else:
        wav_bytes = _synthesize_local(text, lang)

    if output_file:
        Path(output_file).write_bytes(wav_bytes)
        print(f"Saved to {output_file}")

    if not play:
        return wav_bytes

    _play_wav_bytes(wav_bytes)
    return None


if __name__ == "__main__":
    segments = [
        ("Hello, welcome to the demo.", "en"),
        ("Ahora cambiamos al español.", "es"),
        ("Back to English again.", "en"),
    ]
    for i, (text, lang) in enumerate(segments):
        synthesize(text, lang, output_file=f"output_{i}_{lang}.wav", play=True)
