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

        sd.play(audio_data, samplerate=sample_rate)
        sd.wait()


if __name__ == "__main__":
    segments = [
        ("Hello, welcome to the demo.", "en"),
        ("Ahora cambiamos al español.", "es"),
        ("Back to English again.", "en"),
    ]
    for i, (text, lang) in enumerate(segments):
        synthesize(text, lang, output_file=f"output_{i}_{lang}.wav", play=True)


