import os
import platform
import tempfile
import wave
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
# resampler. On WSL the bridge uses a low-quality adaptive converter; on
# native Windows PortAudio handles it, but explicit resampling is still
# cleaner.
_DEVICE_RATE = _output_samplerate()

# On WSL the audio bridge (PulseAudio → WSLg → Windows) has a cold-start
# underrun in the first ~100-300 ms, producing a harsh onset. Prepend silence
# so the transient lands in the preroll, not the first word.
# On native Windows PortAudio primes immediately — no preroll needed.
_PREROLL_SECONDS = 0.0 if platform.system() == "Windows" else 0.5


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

def synthesize(text: str, lang: str, output_file: str | None = None, play: bool = True):
    """
    Synthesize text using the given language ("en" or "es").
    Saves to a WAV file and/or plays the audio.
    """
    voice = voices[lang]

    syn_config = SynthesisConfig(length_scale=_SPEED_SCALE)

    # Generate audio file
    if output_file:
        with wave.open(output_file, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        print(f"Saved to {output_file}")

    # Play audio if requested
    if play:
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

        # On WSL2 the audio bridge cold-starts with an underrun; prepend
        # silence so the distortion lands in the preroll, not the first word.
        # _PREROLL_SECONDS is 0 on native Windows where PortAudio primes
        # immediately.
        if _PREROLL_SECONDS > 0:
            preroll = np.zeros(int(sample_rate * _PREROLL_SECONDS), dtype=np.int16)
            audio_data = np.concatenate([preroll, audio_data])

        play_data = _resample_to(audio_data, sample_rate, _DEVICE_RATE)
        sd.play(play_data, samplerate=_DEVICE_RATE, latency="high")
        sd.wait()


if __name__ == "__main__":
    segments = [
        ("Hello, welcome to the demo.", "en"),
        ("Ahora cambiamos al español.", "es"),
        ("Back to English again.", "en"),
    ]
    for i, (text, lang) in enumerate(segments):
        synthesize(text, lang, output_file=f"output_{i}_{lang}.wav", play=True)


