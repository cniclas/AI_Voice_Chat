import pyaudio
import wave
import sys
import tty
import termios
import threading

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
CHUNK = 1024
OUTPUT_FILENAME = "recording.wav"


def _getch() -> str:
    """Read one keypress from stdin without requiring Enter."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def record_once(output_path: str = OUTPUT_FILENAME) -> str | None:
    """
    SPACE to start recording, SPACE again to stop.
    'q' or Ctrl+C to quit.
    Returns output_path on success, None if user quit.
    """
    state = {"recording": False}
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
            if state["recording"]:
                frames.append(data)

    capture_thread = threading.Thread(target=_capture, daemon=True)
    capture_thread.start()

    print("Press SPACE to start recording. Press 'q' to quit.")
    result_path = None
    try:
        while True:
            ch = _getch()
            if ch == " ":
                if not state["recording"]:
                    state["recording"] = True
                    print("Recording... Press SPACE to stop.")
                else:
                    state["recording"] = False
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
    return result_path


def main():
    print("Push-to-talk audio recorder")
    while True:
        result = record_once(OUTPUT_FILENAME)
        if result is None:
            print("Goodbye!")
            break
        print(f"Audio saved as {OUTPUT_FILENAME}")


if __name__ == "__main__":
    main()
