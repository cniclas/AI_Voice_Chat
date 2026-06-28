import pyaudio
import wave
import sys
import platform
import threading

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
CHUNK = 1024
OUTPUT_FILENAME = "recording.wav"


if platform.system() == "Windows":
    import msvcrt

    def _getch() -> str:
        """Read one keypress without requiring Enter (Windows)."""
        return msvcrt.getwch()
else:
    import tty
    import termios

    def _getch() -> str:
        """Read one keypress from stdin without requiring Enter (Unix)."""
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch


def record_once(output_path: str = OUTPUT_FILENAME) -> tuple[str, str] | None:
    """
    'e' for English, 's' for Spanish to start recording immediately.
    SPACE to stop. 'q' or Ctrl+C to quit.
    Returns (language, output_path) on success, None if user quit.
    """
    # Wait for language selection to start recording
    print("Press 'e' for English, 's' for Spanish, or 'q' to quit.")
    language = None
    try:
        while language is None:
            ch = _getch()
            if ch == "e":
                language = "en"
                print("Recording in English... Press SPACE to stop.")
            elif ch == "s":
                language = "es"
                print("Recording in Spanish... Press SPACE to stop.")
            elif ch in ("q", "Q", "\x03"):  # q or Ctrl+C
                return None
    except KeyboardInterrupt:
        return None

    frames = []
    stop_capture = threading.Event()

    audio = pyaudio.PyAudio()
    stream = audio.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK,
    )

    def _capture():
        while not stop_capture.is_set():
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)

    capture_thread = threading.Thread(target=_capture, daemon=True)
    capture_thread.start()

    result_path = None
    try:
        while True:
            ch = _getch()
            if ch == " ":
                result_path = output_path
                break
            elif ch in ("q", "Q", "\x03"):  # q or Ctrl+C
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop_capture.set()
        capture_thread.join()
        stream.stop_stream()
        stream.close()
        audio.terminate()

    if result_path is None or not frames:
        return None

    wf = wave.open(result_path, "wb")
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(pyaudio.PyAudio().get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b"".join(frames))
    wf.close()
    return (language, result_path)


def main():
    print("Push-to-talk audio recorder")
    while True:
        result = record_once(OUTPUT_FILENAME)
        if result is None:
            print("Goodbye!")
            break
        language, output_path = result
        print(f"Audio saved as {output_path} (Language: {language})")


if __name__ == "__main__":
    main()
