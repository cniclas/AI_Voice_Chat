import io
import os
import tempfile
from pathlib import Path

import numpy as np
import sounddevice as sd
import wave
import warnings
import logging
from kokoro import KPipeline

# Reduce noisy warnings from dependencies during startup.
warnings.filterwarnings(
    "ignore",
    message="dropout option adds dropout after all but last recurrent layer",
)
warnings.filterwarnings("ignore", category=FutureWarning)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("torch").setLevel(logging.ERROR)

# Kokoro-82M's native output sample rate.
_MODEL_RATE = 24000

# One voice per supported language. af_heart and ef_dora are Kokoro's
# top-graded English voice and only female Spanish voice respectively — see
# https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
_VOICES = {
    "en": ("a", "af_heart"),
    "es": ("e", "ef_dora"),
}

# Make the synthesized voice slightly slower and more natural for speech
# playback. Kokoro's speed knob runs the opposite direction of Piper's old
# length_scale, so <1 here is the equivalent of Piper's length_scale=1.08.
_SPEED_SCALE = 0.93

# Load one pipeline per language upfront. Each pipeline lazily downloads (and
# caches under ~/.cache/huggingface) the Kokoro-82M weights plus the
# language's phonemizer data on first use.
_pipelines = {
    lang: KPipeline(lang_code=code, repo_id="hexgrad/Kokoro-82M")
    for lang, (code, _voice) in _VOICES.items()
}


def _output_samplerate(default: int = 44100) -> int:
    """Native sample rate of the default output device."""
    try:
        return int(round(sd.query_devices(kind="output")["default_samplerate"]))
    except Exception:
        return default


# Pre-resample to the device's native rate to avoid quality loss from the OS
# resampler.
_DEVICE_RATE = _output_samplerate()

# A tiny tail of silence makes the final word feel complete instead of clipped.
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


def _synthesize_int16(text: str, lang: str) -> np.ndarray:
    """Run Kokoro over `text` and return mono int16 PCM at _MODEL_RATE."""
    _, voice = _VOICES[lang]
    chunks = [
        np.asarray(audio, dtype=np.float32)
        for _, _, audio in _pipelines[lang](text, voice=voice, speed=_SPEED_SCALE)
    ]
    if not chunks:
        return np.zeros(0, dtype=np.int16)
    samples = np.concatenate(chunks)
    return np.clip(np.round(samples * 32767), -32768, 32767).astype(np.int16)


def _wav_bytes(audio_i16: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(_MODEL_RATE)
        wav_file.writeframes(audio_i16.tobytes())
    return buf.getvalue()


def synthesize(text: str, lang: str, output_file: str | None = None, play: bool = True) -> bytes | None:
    """
    Synthesize text using the given language ("en" or "es").
    Saves to a WAV file and/or plays the audio locally.

    When play=False, returns the synthesized WAV bytes instead of playing
    them locally — used by the browser UI, which owns audio playback
    client-side (so the user can route TTS output to a different device
    than the one used for microphone capture).
    """
    audio_i16 = _synthesize_int16(text, lang)
    wav_bytes = _wav_bytes(audio_i16)

    if output_file:
        Path(output_file).write_bytes(wav_bytes)
        print(f"Saved to {output_file}")

    if not play:
        return wav_bytes

    # Play audio locally (CLI path)
    play_data = _resample_to(audio_i16, _MODEL_RATE, _DEVICE_RATE)
    if _TAIL_SECONDS > 0:
        tail = np.zeros(int(_DEVICE_RATE * _TAIL_SECONDS), dtype=np.int16)
        play_data = np.concatenate([play_data, tail])

    sd.play(play_data, samplerate=_DEVICE_RATE, blocking=True)
    return None


if __name__ == "__main__":
    segments = [
        ("Hello, welcome to the demo.", "en"),
        ("Ahora cambiamos al español.", "es"),
        ("Back to English again.", "en"),
    ]
    for i, (text, lang) in enumerate(segments):
        synthesize(text, lang, output_file=f"output_{i}_{lang}.wav", play=True)
