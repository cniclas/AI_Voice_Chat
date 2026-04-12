import pyaudio
import wave
import time
from pynput import keyboard

# Audio recording parameters
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
CHUNK = 1024
OUTPUT_FILENAME = "recording.wav"

# Global variables
recording = False
frames = []
quit_flag = False

def start_recording():
    global recording, frames
    if not recording:
        recording = True
        frames = []
        print("Recording started... Press space again to stop.")

def stop_recording():
    global recording
    if recording:
        recording = False
        print("Recording stopped. Saving file...")
        save_audio()
        print(f"Audio saved as {OUTPUT_FILENAME}")

def save_audio():
    wf = wave.open(OUTPUT_FILENAME, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(pyaudio.PyAudio().get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b''.join(frames))
    wf.close()

def on_press(key):
    global recording, quit_flag
    try:
        if key == keyboard.Key.space:
            if not recording:
                start_recording()
            else:
                stop_recording()
        elif hasattr(key, 'char') and key.char == 'q':
            if recording:
                stop_recording()
            quit_flag = True
    except AttributeError:
        pass

def main():
    print("Push-to-talk audio recorder")
    print("Press space to start/stop recording. Press 'q' to quit.")

    # Initialize PyAudio
    audio = pyaudio.PyAudio()

    # Open stream
    stream = audio.open(format=FORMAT, channels=CHANNELS,
                        rate=RATE, input=True,
                        frames_per_buffer=CHUNK)

    # Start keyboard listener
    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    try:
        while not quit_flag:
            if recording:
                data = stream.read(CHUNK)
                frames.append(data)
            time.sleep(0.01)  # Small delay to prevent high CPU usage
    except KeyboardInterrupt:
        pass
    finally:
        # Clean up
        stream.stop_stream()
        stream.close()
        audio.terminate()
        listener.stop()

if __name__ == "__main__":
    main()
