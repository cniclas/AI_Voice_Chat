import os
import wave
import numpy as np
import sounddevice as sd
from piper.voice import PiperVoice

_VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")

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


# The Piper voices render at 22050 Hz but the output device runs at a higher
# rate (44100 on WSL). Resampling to the device rate ourselves keeps the
# WSLg audio bridge from doing it with ALSA's low-quality converter.
_DEVICE_RATE = _output_samplerate()


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

    # Generate audio file
    if output_file:
        with wave.open(output_file, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        print(f"Saved to {output_file}")

    # Play audio if requested
    if play:
        # Create a temporary file to read back for playback
        playback_file = output_file if output_file else "/tmp/piper_playback.wav"

        if not output_file:
            with wave.open(playback_file, "wb") as wav_file:
                voice.synthesize_wav(text, wav_file)

        # Read and play the WAV file
        with wave.open(playback_file, "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())
            # Convert bytes to numpy array
            audio_data = np.frombuffer(frames, dtype=np.int16)

        # sd.play() opens a fresh output stream each call. On WSL2 (audio
        # bridged to the Windows audio server) that stream has a cold start:
        # the first ~100-300 ms underruns, producing a harsh/distorted onset
        # that clears once the device buffer primes. Prepend a short silence
        # pre-roll so the warm-up transient lands in silence instead of the
        # first word, and request larger buffers via latency="high".
        preroll = np.zeros(int(sample_rate * 0.5), dtype=np.int16)
        audio_data = np.concatenate([preroll, audio_data])

        # Resample to the device's native rate so the WSLg bridge does no
        # rate conversion of its own (its 22050->44100 adaptive resampling
        # colours the sound and can shift audibly partway through a response).
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


