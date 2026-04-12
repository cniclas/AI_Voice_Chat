import os
import sys
import tty
import termios
import select
import pyaudio
import wave

# WSL2: route audio through WSLg PulseAudio if not already set
if 'PULSE_SERVER' not in os.environ:
    os.environ['PULSE_SERVER'] = 'unix:/mnt/wslg/PulseServer'


class _SuppressAlsaErrors:
    """Suppress noisy ALSA/libc stderr output (harmless WSL2 device enumeration warnings)."""
    def __enter__(self):
        self._devnull = open(os.devnull, 'w')
        self._old_stderr_fd = os.dup(2)
        os.dup2(self._devnull.fileno(), 2)
        return self

    def __exit__(self, *_):
        os.dup2(self._old_stderr_fd, 2)
        os.close(self._old_stderr_fd)
        self._devnull.close()


# Audio recording parameters
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
CHUNK = 1024
OUTPUT_FILENAME = "recording.wav"


def save_audio(frames):
    wf = wave.open(OUTPUT_FILENAME, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(pyaudio.PyAudio().get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b''.join(frames))
    wf.close()


def read_key(fd):
    """Return a single keypress from stdin without blocking, or None."""
    if select.select([fd], [], [], 0)[0]:
        return os.read(fd, 1)
    return None


def main():
    print("Push-to-talk audio recorder")
    print("Press space to start/stop recording. Press 'q' to quit.")

    with _SuppressAlsaErrors():
        audio = pyaudio.PyAudio()

    try:
        with _SuppressAlsaErrors():
            stream = audio.open(format=FORMAT, channels=CHANNELS,
                                rate=RATE, input=True,
                                frames_per_buffer=CHUNK)
    except OSError as e:
        print(f"Error: Unable to access audio input device. {e}")
        print("On WSL2: run 'sudo apt-get install -y libasound2-plugins pulseaudio-utils'")
        print("Ensure ~/.asoundrc routes default to pulse and PULSE_SERVER is set.")
        audio.terminate()
        return

    recording = False
    frames = []
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setraw(fd)
        while True:
            key = read_key(fd)
            if key == b' ':
                if not recording:
                    recording = True
                    frames = []
                    sys.stdout.write("\rRecording... press space to stop.   \r\n")
                    sys.stdout.flush()
                else:
                    recording = False
                    sys.stdout.write("\rStopped. Saving...                   \r\n")
                    sys.stdout.flush()
                    save_audio(frames)
                    sys.stdout.write(f"Saved as {OUTPUT_FILENAME}\r\n")
                    sys.stdout.flush()
            elif key in (b'q', b'Q', b'\x03'):  # q, Q, or Ctrl+C
                break

            if recording:
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        stream.stop_stream()
        stream.close()
        audio.terminate()
        print()


if __name__ == "__main__":
    main()
