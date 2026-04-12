# Audio Recorder

A simple push-to-talk audio recording script using Python.

## Features

- Press spacebar to start recording from microphone
- Press spacebar again to stop recording and save the audio file
- Press 'q' to quit the program (will stop recording if active and save)
- Saves audio as WAV file (recording.wav)

## Requirements

- Python 3.x
- Microphone access

## Installation

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. On Linux, you may need to install PyAudio system dependencies:
   ```
   sudo apt-get install python3-pyaudio python3-dev portaudio19-dev
   ```

## Usage

Run the script:
```
python record.py
```

- Press space to start recording
- Press space again to stop and save
- Press Ctrl+C or 'q' to quit without saving

## Notes

- Audio is recorded in mono at 44.1kHz
- The script uses pynput for keyboard detection and pyaudio for audio recording