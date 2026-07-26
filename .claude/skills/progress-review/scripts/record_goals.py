"""Write a validated goals block into the persistent student profile.

Goals are shared state: `skill_refs.pick_focus()` hands an active focus goal
straight to the lesson builder, and `curriculum.top_vocab_to_practice()` promotes
goal words into the generated story. A malformed goal doesn't crash anything --
it is silently ignored, and the student practices something else for a month
without anyone noticing. So everything is checked before a byte is written.

    uv run python .claude/skills/progress-review/scripts/record_goals.py goals.json --user niclas --dry-run
    uv run python .claude/skills/progress-review/scripts/record_goals.py goals.json --user niclas

Expected JSON -- either a bare list of goals, or the full block:

    {
      "level_target": "B2",
      "active": [
        {"id": "por-para", "kind": "focus",
         "statement": "Use por and para correctly without stopping to think.",
         "focus": "Por vs para",
         "measure": "No por/para weakness recorded across 3 consecutive sessions that drilled it",
         "created": "2026-07-26", "target_date": "2026-09-01", "status": "active",
         "baseline": {"occurrences": 7, "last_seen": "2026-07-20"}}
      ],
      "archive": []
    }

Replaces the goals block wholesale rather than merging, because a review decides
the whole set -- goals that were met move to `archive`, and a merge would quietly
resurrect the ones the reviewer meant to drop. Pass `--dry-run` first.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

VALID_KINDS = {"focus", "vocab", "habit", "level"}
VALID_STATUSES = {"active", "met", "stalled", "dropped"}
REQUIRED = ("id", "kind", "statement", "measure")


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "curriculum.py").exists():
            return parent
    raise SystemExit("Could not locate the repo root (no curriculum.py found above this script).")


def _valid_date(value) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def _validate(block: dict, known_focuses: list[str], normalize) -> list[str]:
    problems = []
    wanted = {normalize(f): f for f in known_focuses}

    for key in ("active", "archive"):
        if key in block and not isinstance(block[key], list):
            problems.append(f"'{key}' must be a list")

    seen_ids = set()
    for i, goal in enumerate(block.get("active", [])):
        where = f"active[{i}]"
        if not isinstance(goal, dict):
            problems.append(f"{where} must be an object")
            continue

        for field in REQUIRED:
            if not isinstance(goal.get(field), str) or not goal[field].strip():
                problems.append(f"{where} is missing '{field}'")

        goal_id = goal.get("id")
        if isinstance(goal_id, str):
            if goal_id in seen_ids:
                problems.append(f"{where} repeats id {goal_id!r}; ids identify goals across reviews")
            seen_ids.add(goal_id)

        kind = goal.get("kind")
        if kind is not None and kind not in VALID_KINDS:
            problems.append(f"{where}['kind'] is {kind!r}, expected one of {sorted(VALID_KINDS)}")

        status = goal.get("status", "active")
        if status not in VALID_STATUSES:
            problems.append(f"{where}['status'] is {status!r}, expected one of {sorted(VALID_STATUSES)}")

        for field in ("created", "target_date"):
            if field in goal and not _valid_date(goal[field]):
                problems.append(f"{where}['{field}'] must be YYYY-MM-DD, got {goal[field]!r}")

        # The check that actually matters: an unrecognized focus name makes the
        # goal inert, and nothing downstream would ever complain.
        if kind == "focus":
            focus = goal.get("focus")
            if not isinstance(focus, str) or not focus.strip():
                problems.append(f"{where} is kind 'focus' but has no 'focus' name")
            elif normalize(focus) not in wanted:
                match = ", ".join(known_focuses) or "(none found — is the language-lesson skill present?)"
                problems.append(
                    f"{where}['focus'] is {focus!r}, which the language file does not document, "
                    f"so the lesson builder would ignore it. Expected one of: {match}")

        if kind == "vocab":
            words = goal.get("words")
            if not isinstance(words, list) or not words or any(
                    not isinstance(x, str) or not x.strip() for x in words):
                problems.append(f"{where} is kind 'vocab' but 'words' is not a non-empty list of strings")

        if "topics" in goal and (
                not isinstance(goal["topics"], list)
                or any(not isinstance(t, str) or not t.strip() for t in goal["topics"])):
            problems.append(f"{where}['topics'] must be a list of weakness topic strings")

        if "checkpoints" in goal and not isinstance(goal["checkpoints"], list):
            problems.append(f"{where}['checkpoints'] must be a list")

    if "level_target" in block and not isinstance(block["level_target"], (str, type(None))):
        problems.append("'level_target' must be a string")

    return problems


def _describe_changes(before: dict, after: dict) -> list[str]:
    old = {g.get("id"): g for g in before.get("active", []) if isinstance(g, dict)}
    new = {g.get("id"): g for g in after.get("active", []) if isinstance(g, dict)}
    lines = []

    for goal_id, goal in new.items():
        if goal_id not in old:
            lines.append(f"  + goal: {goal_id} ({goal.get('kind')}) — {goal.get('statement')}")
        elif old[goal_id].get("status") != goal.get("status"):
            lines.append(f"  ~ goal: {goal_id} {old[goal_id].get('status')} -> {goal.get('status')}")
        elif len(old[goal_id].get("checkpoints", [])) != len(goal.get("checkpoints", [])):
            lines.append(f"  ~ goal: {goal_id} checkpoint added")

    for goal_id in old:
        if goal_id not in new:
            lines.append(f"  - goal: {goal_id} (no longer active)")

    old_archive = len(before.get("archive", []))
    new_archive = len(after.get("archive", []))
    if new_archive != old_archive:
        lines.append(f"  ~ archive: {old_archive} -> {new_archive}")

    if before.get("level_target") != after.get("level_target"):
        lines.append(f"  ~ level_target: {before.get('level_target')} -> {after.get('level_target')}")

    return lines


def main() -> int:
    sys.path.insert(0, str(_repo_root()))
    from users import USERS, get_user  # noqa: E402

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("goals", type=Path, help="Path to the goals JSON.")
    parser.add_argument("--user", required=True, choices=sorted(USERS),
                        help="Which student's profile to update.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing the profile.")
    args = parser.parse_args()
    user = get_user(args.user)

    if not args.goals.exists():
        raise SystemExit(f"No such file: {args.goals}")

    try:
        payload = json.loads(args.goals.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{args.goals} is not valid JSON: {e}")

    # A bare list is the common case when only the active goals are changing.
    block = {"active": payload} if isinstance(payload, list) else payload
    if not isinstance(block, dict):
        raise SystemExit("Goals JSON must be an object or a list of goals.")

    import skill_refs  # noqa: E402
    from skill_refs import _normalize  # noqa: E402

    # Validated against the student's own target language: a French focus name
    # is not a typo for a Spanish one, it belongs to a different file.
    problems = _validate(block, skill_refs.focus_names(user.target_lang), _normalize)
    if problems:
        raise SystemExit("Goals JSON is malformed:\n" + "\n".join(f"  - {p}" for p in problems))

    from curriculum import load_profile, save_profile  # noqa: E402

    profile = load_profile(user.profile_path, user.target_lang, user.native_lang)
    before = json.loads(json.dumps(profile.get("goals") or {"active": [], "archive": []}))

    after = {
        "updated": date.today().isoformat(),
        "level_target": block.get("level_target", before.get("level_target")),
        "active": block.get("active", []),
        "archive": block.get("archive", before.get("archive", [])),
    }

    changes = _describe_changes(before, after)
    if args.dry_run:
        print("Dry run -- profile not written. Changes would be:")
        print("\n".join(changes) if changes else "  (none)")
        return 0

    profile["goals"] = after
    save_profile(profile, user.profile_path)
    print(f"Wrote goals to {user.profile_path}:")
    print("\n".join(changes) if changes else "  (no structural change; timestamp refreshed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
