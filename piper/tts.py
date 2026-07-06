import io
import os
import tempfile
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from piper.config import SynthesisConfig
from piper.voice import PiperVoice

_VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")

# Make the synthesized voice slightly slower and more natural for speech playback.
_SPEED_SCALE = 1.08

# Load both voices upfront
voices = {
    "en": PiperVoice.load(os.path.join(_VOICES_DIR, "en_US-lessac-medium.onnx")),
    "es": PiperVoice.load(os.path.join(_VOICES_DIR, "es_MX-claude-high.onnx")),
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

def synthesize(text: str, lang: str, output_file: str | None = None, play: bool = True) -> bytes | None:
    """
    Synthesize text using the given language ("en" or "es").
    Saves to a WAV file and/or plays the audio locally.

    When play=False, returns the synthesized WAV bytes instead of playing
    them locally — used by the browser UI, which owns audio playback
    client-side (so the user can route TTS output to a different device
    than the one used for microphone capture).
    """
    voice = voices[lang]

    syn_config = SynthesisConfig(length_scale=_SPEED_SCALE)

    # Generate audio file
    if output_file:
        with wave.open(output_file, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        print(f"Saved to {output_file}")

    if not play:
        if output_file:
            return Path(output_file).read_bytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        return buf.getvalue()

    # Play audio locally (CLI path)
    playback_file = output_file if output_file else os.path.join(
        tempfile.gettempdir(), "piper_playback.wav"
    )

    if not output_file:
        with wave.open(playback_file, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file, syn_config=syn_config)

    with wave.open(playback_file, "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
        audio_data = np.frombuffer(frames, dtype=np.int16)

    play_data = _resample_to(audio_data, sample_rate, _DEVICE_RATE)
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


