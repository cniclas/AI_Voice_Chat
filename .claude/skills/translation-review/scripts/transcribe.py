"""Transcribe a spoken translation to text, using the same Whisper setup as the app.

Reuses `session_core.transcribe_audio()` so the transcript matches what a live
session would have produced from the same audio -- same 16 kHz mono conversion,
same model, same post-processing.

    uv run python .claude/skills/translation-review/scripts/transcribe.py recording.wav
    uv run python .claude/skills/translation-review/scripts/transcribe.py recording.wav --model base

Default model is large-v3 (what main.py and web/app_factory.py load). It is slow
to load cold; --model base is fine for a rough pass, at the cost of more
mishearings that then have to be filtered as noise during review.
"""

import argparse
import sys
from pathlib import Path


def _repo_root() -> Path:
    """Walk up from this file until curriculum.py appears, rather than counting
    parent directories -- the skill can be moved without breaking the import."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "curriculum.py").exists():
            return parent
    raise SystemExit("Could not locate the repo root (no curriculum.py found above this script).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("audio", type=Path, help="Path to the WAV file to transcribe.")
    parser.add_argument("--model", default="large-v3", help="Whisper model name (default: large-v3).")
    parser.add_argument("--language", default="en",
                        help="Language spoken in the audio (default: en -- the student is "
                             "translating into English; auto-detect tends to hear Spanish in "
                             "the proper nouns).")
    parser.add_argument("-o", "--output", type=Path,
                        help="Write the transcript here as well as to stdout.")
    args = parser.parse_args()

    if not args.audio.exists():
        raise SystemExit(f"No such audio file: {args.audio}")

    sys.path.insert(0, str(_repo_root()))
    import whisper  # noqa: E402  -- import deferred until sys.path is set up
    from session_core import transcribe_audio  # noqa: E402

    print(f"Loading Whisper {args.model}...", file=sys.stderr)
    model = whisper.load_model(args.model)
    text = transcribe_audio(str(args.audio), model, language=args.language)

    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
