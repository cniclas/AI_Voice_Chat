import os
import wave
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
    Optionally saves to a WAV file and/or plays directly.
    """
    voice = voices[lang]

    if output_file:
        with wave.open(output_file, "w") as wav_file:
            voice.synthesize_wav(text, wav_file)
        print(f"Saved to {output_file}")

    if play:
        stream = sd.OutputStream(
            samplerate=voice.config.sample_rate,
            channels=1,
            dtype="int16",
        )
        stream.start()
        for chunk in voice.synthesize(text):
            stream.write(chunk.audio_int16_array)
        stream.stop()
        stream.close()


if __name__ == "__main__":
    segments = [
        ("Hello, welcome to the demo.", "en"),
        ("Ahora cambiamos al español.", "es"),
        ("Back to English again.", "en"),
    ]
    for i, (text, lang) in enumerate(segments):
        synthesize(text, lang, output_file=f"output_{i}_{lang}.wav", play=True)
