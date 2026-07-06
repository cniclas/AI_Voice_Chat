# Audio Recorder

A simple push-to-talk audio recording script using Python.

## Features

- Press spacebar to start recording from microphone
- Press spacebar again to stop recording and save the audio file
- Press 'q' to quit the program (will stop recording if active and save)
- Saves audio as WAV file (recording.wav)

## Requirements

- Python 3.x (Windows only)
- Microphone access

## Installation

1. Install dependencies:
   ```
   pip install -r requirements.txt
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
- The script uses `msvcrt` for keyboard detection and `pyaudio` for audio recording