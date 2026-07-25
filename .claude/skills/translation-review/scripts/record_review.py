"""Merge a translation review into the persistent student profile.

Takes findings in the same JSON shape `curriculum.analyze_weaknesses()` produces
and merges them via `curriculum.merge_analysis_into_profile()`, so a weakness
found by reading counts toward the same `occurrences` tally as one found by
speaking. Hand-editing recordings/student_profile.json instead would silently
diverge from how the session pipeline counts things.

    uv run python .claude/skills/translation-review/scripts/record_review.py review.json
    uv run python .claude/skills/translation-review/scripts/record_review.py review.json --dry-run

Expected JSON:

    {
      "summary": "2-3 sentences on the translation.",
      "weaknesses": [
        {"type": "grammar", "topic": "indirect object pronouns",
         "evidence": "sold him the house", "correction": "sold the house to him",
         "explanation": "le marks the recipient, not the thing sold."}
      ],
      "vocab_to_practice": ["actualmente", "soportar"]
    }
"""

import argparse
import json
import sys
from pathlib import Path

VALID_TYPES = {"grammar", "vocabulary", "expression"}


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "curriculum.py").exists():
            return parent
    raise SystemExit("Could not locate the repo root (no curriculum.py found above this script).")


def _validate(review: dict) -> list[str]:
    """Return a list of problems. Checking up front beats writing a malformed
    profile that the session pipeline then has to cope with."""
    problems = []
    if not isinstance(review.get("summary"), str):
        problems.append("'summary' must be a string")

    weaknesses = review.get("weaknesses")
    if not isinstance(weaknesses, list):
        problems.append("'weaknesses' must be a list")
    else:
        for i, w in enumerate(weaknesses):
            if not isinstance(w, dict):
                problems.append(f"weaknesses[{i}] must be an object")
                continue
            for field in ("type", "topic", "evidence", "correction", "explanation"):
                if not isinstance(w.get(field), str) or not w[field].strip():
                    problems.append(f"weaknesses[{i}] is missing '{field}'")
            if w.get("type") in VALID_TYPES or w.get("type") is None:
                continue
            problems.append(
                f"weaknesses[{i}]['type'] is {w['type']!r}, expected one of {sorted(VALID_TYPES)}"
            )

    vocab = review.get("vocab_to_practice")
    if not isinstance(vocab, list) or any(not isinstance(v, str) for v in vocab):
        problems.append("'vocab_to_practice' must be a list of strings")

    return problems


def _describe_changes(before: dict, after: dict) -> list[str]:
    """Human-readable diff of the profile, so the caller can report what landed
    in shared state rather than asking the user to trust it."""
    lines = []

    before_w = {(w.get("type"), w.get("topic", "").casefold()): w.get("occurrences", 0)
                for w in before.get("weaknesses", [])}
    for w in after.get("weaknesses", []):
        key = (w.get("type"), w.get("topic", "").casefold())
        count = w.get("occurrences", 0)
        if key not in before_w:
            lines.append(f"  + weakness: {w.get('topic')} ({w.get('type')})")
        elif count > before_w[key]:
            lines.append(f"  ~ weakness: {w.get('topic')} -> seen {count}x")

    before_v = {v.get("word", "").casefold() for v in before.get("vocab_to_practice", [])}
    for v in after.get("vocab_to_practice", []):
        if v.get("word", "").casefold() not in before_v:
            lines.append(f"  + vocab: {v.get('word')}")

    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("review", type=Path, help="Path to the review JSON.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing the profile.")
    args = parser.parse_args()

    if not args.review.exists():
        raise SystemExit(f"No such file: {args.review}")

    try:
        review = json.loads(args.review.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{args.review} is not valid JSON: {e}")

    problems = _validate(review)
    if problems:
        raise SystemExit("Review JSON is malformed:\n" + "\n".join(f"  - {p}" for p in problems))

    if not review["weaknesses"] and not review["vocab_to_practice"]:
        print("Nothing to record -- no weaknesses and no vocabulary in this review.")
        return 0

    sys.path.insert(0, str(_repo_root()))
    from curriculum import load_profile, merge_analysis_into_profile, save_profile  # noqa: E402

    profile = load_profile()
    before = json.loads(json.dumps(profile))  # deep copy for the diff
    merge_analysis_into_profile(profile, review)

    changes = _describe_changes(before, profile)
    if args.dry_run:
        print("Dry run -- profile not written. Changes would be:")
        print("\n".join(changes) if changes else "  (none)")
        return 0

    save_profile(profile)
    print("Recorded in the student profile:")
    print("\n".join(changes) if changes else "  (no new entries; existing ones refreshed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
