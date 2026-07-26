"""Aggregate a student's profile into a progress snapshot.

Read-only. Turns `recordings/<user>/student_profile.json` into the numbers a
progress review needs and cannot get by reading the file: for every weakness,
how many sessions have run since it was last seen and how many of those would
have exposed it; which weaknesses have never been practiced as a focus at all;
which vocabulary the pipeline flagged and never used; where the profile's
truncation ceilings are currently binding.

The arithmetic is deterministic and dull, which is exactly why it belongs in a
script -- done by eye across sixty log entries it comes out different every time.

    uv run python .claude/skills/progress-review/scripts/progress_snapshot.py --user niclas
    uv run python .claude/skills/progress-review/scripts/progress_snapshot.py --user niclas --since 2026-05-01
    uv run python .claude/skills/progress-review/scripts/progress_snapshot.py --user niclas --json

Deliberately reports `sessions_since`, `focused_sessions_since` and
`substantial_sessions_since` separately rather than collapsing them into one
"was it exposed?" verdict. Whether a conversation exposes a weakness depends on
whether the student can route around the structure -- unavoidable things like
ser/estar surface in any real exchange, while the subjunctive needs to be
targeted -- and that is a judgment the reader makes, not the script.
"""

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path

# Caps applied by curriculum.py when it writes these lists. A list sitting at
# its cap has been silently dropping entries, which changes what absence means.
CEILINGS = {
    "weaknesses": 30,
    "practice_log": 60,
    "articles_covered": 60,
    "vocab_to_practice": 40,
}

# A session with at least this many spoken turns is long enough that an
# unavoidable structure would plausibly have come up.
SUBSTANTIAL_TURNS = 8


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "curriculum.py").exists():
            return parent
    raise SystemExit("Could not locate the repo root (no curriculum.py found above this script).")


def _parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _related(topic: str, focus: str, normalize) -> bool:
    """Whether a free-text weakness topic and a focus name refer to the same
    thing, by the same stem comparison `skill_refs.pick_focus()` uses -- so the
    blind-spot join agrees with what the lesson builder would actually pick."""
    if not topic or not focus:
        return False
    topic_tokens = {t for t in normalize(topic).split() if len(t) >= 4}
    focus_tokens = set(normalize(focus).split())
    if not topic_tokens or not focus_tokens:
        return False
    return any(
        any(t.startswith(f[:5]) or f.startswith(t[:5]) for f in focus_tokens)
        for t in topic_tokens
    )


def _cadence(entries: list[dict]) -> dict:
    dates = sorted({d for d in (_parse_date(e.get("date")) for e in entries) if d})
    if len(dates) < 2:
        return {"days_between_median": None, "longest_gap_days": None,
                "first": dates[0].isoformat() if dates else None,
                "last": dates[-1].isoformat() if dates else None}
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    return {
        "days_between_median": statistics.median(gaps),
        "longest_gap_days": max(gaps),
        "first": dates[0].isoformat(),
        "last": dates[-1].isoformat(),
    }


def analyze(profile: dict, since: date | None, normalize, target: str) -> dict:
    log = [e for e in profile.get("practice_log", []) if isinstance(e, dict)]
    if since:
        log = [e for e in log if (_parse_date(e.get("date")) or date.min) >= since]

    practiced = profile.get("focuses_practiced", [])
    practiced_names = [f.get("focus", "") for f in practiced if isinstance(f, dict)]

    weaknesses = []
    for w in profile.get("weaknesses", []):
        if not isinstance(w, dict):
            continue
        last_seen = _parse_date(w.get("last_seen"))
        topic = w.get("topic", "")

        after = [e for e in log if (_parse_date(e.get("date")) or date.min) > (last_seen or date.min)]
        focused = [e for e in after if _related(topic, e.get("focus") or "", normalize)]
        substantial = [e for e in after if (e.get("spoken_turns") or 0) >= SUBSTANTIAL_TURNS]

        drilled = next(
            (f for f in practiced if _related(topic, f.get("focus", ""), normalize)), None)

        weaknesses.append({
            "type": w.get("type", ""),
            "topic": topic,
            "occurrences": w.get("occurrences", 0),
            "first_seen": w.get("first_seen"),
            "last_seen": w.get("last_seen"),
            "sessions_since": len(after),
            "focused_sessions_since": len(focused),
            "substantial_sessions_since": len(substantial),
            "times_drilled": (drilled or {}).get("times", 0),
            "never_practiced": drilled is None,
        })

    vocab = [v for v in profile.get("vocab_to_practice", []) if isinstance(v, dict)]

    return {
        "user_level": profile.get("level"),
        "window": {"since": since.isoformat() if since else None, "sessions": len(log)},
        "cadence": _cadence(log),
        "modes": {
            "challenge": sum(1 for e in log if e.get("mode") == "challenge"),
            "translated": sum(1 for e in log if e.get("translated")),
            "median_spoken_turns": (
                statistics.median([e.get("spoken_turns") or 0 for e in log]) if log else None),
        },
        "weaknesses": sorted(weaknesses, key=lambda w: w["occurrences"], reverse=True),
        "blind_spots": [w for w in weaknesses if w["never_practiced"]],
        "focuses_practiced": practiced,
        "focuses_never_practiced": [
            name for name in _known_focuses(target)
            if not any(normalize(name) == normalize(p) for p in practiced_names)
        ],
        "vocab_never_targeted": [
            {"word": v.get("word"), "last_seen": v.get("last_seen")}
            for v in vocab if not v.get("times_targeted")
        ],
        "goals": _goal_status(profile, log, weaknesses, normalize),
        "ceilings_binding": {
            key: len(profile.get(key, [])) for key, cap in CEILINGS.items()
            if len(profile.get(key, [])) >= cap
        },
    }


def _known_focuses(target: str) -> list[str]:
    """The focus patterns documented for the language this student is learning.
    Passing the target explicitly matters: reading the wrong language's file
    would make every weakness look like a blind spot."""
    import skill_refs
    return skill_refs.focus_names(target)


def _goal_status(profile: dict, log: list[dict], weaknesses: list[dict], normalize) -> dict:
    goals = profile.get("goals") or {}
    vocab = {v.get("word", "").casefold(): v
             for v in profile.get("vocab_to_practice", []) if isinstance(v, dict)}

    rows = []
    for goal in goals.get("active", []):
        if not isinstance(goal, dict):
            continue
        created = _parse_date(goal.get("created"))
        since_created = [e for e in log
                         if (_parse_date(e.get("date")) or date.min) >= (created or date.min)]
        row = {
            "id": goal.get("id"),
            "kind": goal.get("kind"),
            "statement": goal.get("statement"),
            "status": goal.get("status", "active"),
            "created": goal.get("created"),
            "target_date": goal.get("target_date"),
            "baseline": goal.get("baseline"),
            "sessions_since_created": len(since_created),
            "checkpoints": len(goal.get("checkpoints", [])),
        }
        if goal.get("kind") == "focus":
            focus = goal.get("focus") or ""
            # A goal may name the weakness topics it covers explicitly. Needed
            # because stem-matching a focus name against free-text topics is the
            # very thing that creates blind spots -- "using ser for temporary
            # states" never matches "Ser vs estar", so a goal aimed straight at
            # it would otherwise look like it had nothing to track.
            declared = [t for t in goal.get("topics", []) if isinstance(t, str)]
            matching = [
                w for w in weaknesses
                if _related(w["topic"], focus, normalize)
                or any(normalize(w["topic"]) == normalize(t) for t in declared)
            ]
            row["current"] = {
                # None, not 0: no matching weakness means the goal could not be
                # measured, which must not be read as "the weakness is gone".
                "occurrences": max((w["occurrences"] for w in matching), default=None),
                "last_seen": max((w["last_seen"] or "" for w in matching), default=None) or None,
                "topics": [w["topic"] for w in matching],
                "matched": bool(matching),
            }
            row["focused_sessions_since_created"] = sum(
                1 for e in since_created if _related(focus, e.get("focus") or "", normalize))
        elif goal.get("kind") == "vocab":
            row["current"] = {
                "words": {
                    word: vocab.get(word.casefold(), {}).get("times_targeted", 0)
                    for word in goal.get("words", [])
                }
            }
        rows.append(row)

    return {"level_target": goals.get("level_target"), "updated": goals.get("updated"),
            "active": rows, "archived": len(goals.get("archive", []))}


def render(s: dict) -> str:
    out = []
    w = s["window"]
    out.append(f"Level: {s['user_level']}   Sessions in window: {w['sessions']}"
               + (f"   (since {w['since']})" if w["since"] else ""))

    c = s["cadence"]
    if c["days_between_median"] is not None:
        out.append(f"Cadence: {c['first']} -> {c['last']}, median {c['days_between_median']:.0f} "
                   f"days between sessions, longest gap {c['longest_gap_days']} days")
    m = s["modes"]
    out.append(f"Modes: {m['challenge']} challenge, {m['translated']} translated, "
               f"median {m['median_spoken_turns']} spoken turns")

    out.append("\nWeaknesses (occurrences / since last seen: sessions, focused, substantial):")
    if not s["weaknesses"]:
        out.append("  (none recorded)")
    for x in s["weaknesses"]:
        out.append(
            f"  {x['occurrences']:>3}x  {x['topic']} [{x['type']}]\n"
            f"        first {x['first_seen']}, last {x['last_seen']}; since: "
            f"{x['sessions_since']} sessions, {x['focused_sessions_since']} focused, "
            f"{x['substantial_sessions_since']} substantial; drilled {x['times_drilled']}x")

    if s["blind_spots"]:
        out.append("\nBlind spots (never practiced as a focus):")
        for x in s["blind_spots"]:
            out.append(f"  {x['occurrences']:>3}x  {x['topic']}")

    if s["focuses_never_practiced"]:
        out.append("\nFocuses never practiced: " + ", ".join(s["focuses_never_practiced"]))

    if s["vocab_never_targeted"]:
        out.append("\nVocabulary flagged but never woven into a story:")
        out.append("  " + ", ".join(v["word"] for v in s["vocab_never_targeted"]))

    g = s["goals"]
    out.append(f"\nGoals (level target: {g['level_target']}, archived: {g['archived']}):")
    if not g["active"]:
        out.append("  (none set)")
    for row in g["active"]:
        out.append(f"  [{row['status']}] {row['id']} ({row['kind']}) — {row['statement']}")
        out.append(f"        created {row['created']}, target {row['target_date']}, "
                   f"{row['sessions_since_created']} sessions since, "
                   f"{row['checkpoints']} checkpoints")
        if row.get("baseline"):
            out.append(f"        baseline: {row['baseline']}")
        if row.get("current"):
            out.append(f"        current:  {row['current']}")
            if row["current"].get("matched") is False:
                out.append("        NOT MEASURABLE: no weakness topic matches this goal. "
                           "Add a 'topics' list to bind it, or the goal cannot be scored.")
        if "focused_sessions_since_created" in row:
            out.append(f"        focused sessions since created: "
                       f"{row['focused_sessions_since_created']}")

    if s["ceilings_binding"]:
        out.append("\nTruncation ceilings currently binding — absence is not evidence here:")
        for key, n in s["ceilings_binding"].items():
            out.append(f"  {key}: {n} entries (cap {CEILINGS[key]})")

    return "\n".join(out)


def main() -> int:
    sys.path.insert(0, str(_repo_root()))
    from users import USERS, get_user  # noqa: E402

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--user", required=True, choices=sorted(USERS),
                        help="Which student's profile to summarize.")
    parser.add_argument("--since", help="Only count sessions on or after this date (YYYY-MM-DD).")
    parser.add_argument("--json", action="store_true", help="Emit the structured snapshot.")
    args = parser.parse_args()

    since = None
    if args.since:
        since = _parse_date(args.since)
        if since is None:
            raise SystemExit(f"--since must be YYYY-MM-DD, got {args.since!r}")

    user = get_user(args.user)
    if not user.profile_path.exists():
        raise SystemExit(
            f"No profile yet at {user.profile_path} — {user.display_name} has not "
            "completed a session, so there is nothing to review.")

    from curriculum import load_profile  # noqa: E402
    from skill_refs import _normalize  # noqa: E402  (shared so the join matches pick_focus)

    # Read-only: pass no target to load_profile so a snapshot never archives
    # anything, but do use the user's target for the focus join below.
    snapshot = analyze(load_profile(user.profile_path), since, _normalize,
                       user.target_lang)
    print(json.dumps(snapshot, indent=2, ensure_ascii=False) if args.json else render(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
