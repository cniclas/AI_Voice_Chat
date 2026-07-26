# Setting goals that the app will actually act on

A goal here is not a motivational statement. It is a small piece of shared state
that changes what the session pipeline generates tomorrow, so it has to be
written in terms the pipeline can read.

## Where goals live

A top-level `"goals"` key in `recordings/<user>/student_profile.json`:

```json
"goals": {
  "updated": "2026-07-26",
  "level_target": "B2",
  "active": [
    {
      "id": "por-para",
      "kind": "focus",
      "statement": "Use por and para correctly without stopping to think.",
      "focus": "Por vs para",
      "measure": "No por/para weakness recorded across 3 consecutive sessions",
      "created": "2026-07-26",
      "target_date": "2026-09-01",
      "status": "active",
      "baseline": {"occurrences": 7, "last_seen": "2026-07-20"},
      "checkpoints": [{"date": "2026-08-10", "occurrences": 8, "note": "still going wrong in narration"}]
    }
  ],
  "archive": []
}
```

Sharing the profile file rather than sitting beside it is deliberate: it is the
one piece of state every part of the app already loads, and it survives writes
untouched. `save_profile()` dumps the whole dict, and `merge_analysis_into_profile()`
and `record_practice()` only reach into their own keys via `setdefault`, so
nothing in the session pipeline can clobber `goals`.

## Fields

| Field | Why it exists |
|---|---|
| `id` | Stable slug, so checkpoints and archive entries can be matched across reviews. |
| `kind` | `focus` \| `vocab` \| `habit` \| `level`. Decides which part of the app reads it. |
| `statement` | Plain language, for the student. The only field written for a human. |
| `focus` / `words` | The machine-readable payload. `focus` for a focus goal, `words` for a vocab goal. |
| `topics` | Optional, focus goals only. The exact `weaknesses[].topic` strings this goal is tracking — see below. |
| `measure` | How you will know it is met, in terms of profile fields. |
| `baseline` | The numbers at the moment the goal was set — without this, the next review has nothing to compare against and the goal is unmeasurable in practice. |
| `checkpoints` | What each review found. This is the progress record; the report is just its presentation. |
| `status` | `active` \| `met` \| `stalled` \| `dropped`. |

## What the app does with each kind

- **`focus`** — `skill_refs.pick_focus()` returns the goal's focus outright,
  ahead of its usual stem-matching against the top weakness. Today's translation
  challenge is then built around it, and the tutor's system prompt steers
  conversation toward situations needing the structure.
- **`vocab`** — `curriculum.top_vocab_to_practice()` promotes `words` to the
  front of the queue, so they get woven into the generated story even if the
  profile has never logged them.
- **`habit`** — nothing automatic. Measured off `practice_log` cadence at the
  next review.
- **`level`** — advisory only; `level_target` is read by this skill, never by the
  lesson builder. See below.

Only the first active goal of each kind is consulted, so ordering within `active`
is meaningful. Put the one that matters most first.

## A focus goal has to name a canonical focus

`focus` must match one of the `###` headings under `## Focus patterns` in the
student's **target-language** file,
`.claude/skills/language-lesson/references/languages/<target>.md` — the same
names `skill_refs.focus_names(target)` enumerates. Read them out of the file
rather than from memory; they differ per language and the list here would go
stale:

```bash
uv run python -c "import skill_refs, users; u=users.get_user('<user>'); print(*skill_refs.focus_names(u.target_lang), sep='\n')"
```

Matching is exact (after accent- and case-folding), because a goal is an explicit
instruction and guessing at a near-miss would be worse than ignoring it. A goal
naming anything else is inert — the lesson builder falls back to the weakness
picker and nothing visible goes wrong, which is exactly why `record_goals.py`
rejects it up front.

If the thing the student needs isn't in that list, the honest move is to add a
focus pattern to that language file — the language-lesson skill documents how,
and the app reads the file at runtime, so a new pattern works with no code
change.

### Bind the goal to its weakness topics

A focus goal usually exists *because* of a blind spot, and a blind spot is by
definition a weakness whose topic doesn't stem-match any focus name. So the goal
frequently cannot find the very weakness it was created to fix — the snapshot
reports it as unmeasurable rather than guessing.

Add the exact topic strings when setting such a goal:

```json
{"id": "ser-estar", "kind": "focus", "focus": "Ser vs estar",
 "topics": ["using ser for temporary states"], ...}
```

Copy them verbatim from the snapshot's weakness list. Without this, `current`
comes back as `matched: false` and the goal can't be scored at the next review —
which is a far better failure than reporting `occurrences: 0` and letting the
student believe a untouched weakness was fixed.

## Measurable against fields that exist

The test: could a script decide whether this goal is met by reading the profile?

| Not workable | Workable |
|---|---|
| "Speak more confidently" | "Median `spoken_turns` above 12 across the next six sessions" |
| "Get better at the subjunctive" | "No present-subjunctive weakness recorded across 3 consecutive sessions that drilled it" |
| "Learn more vocabulary" | "These 8 words each reach `times_targeted` ≥ 2" |
| "Read more carefully" | "Two translation challenges a week, `translated: true`" |

The right-hand column is not pedantry. A goal that can only be assessed by
opinion will be assessed by opinion, and the next review will grade it on
whatever mood the conversation is in.

Note the shape of the subjunctive example: **consecutive sessions that drilled
it**, not consecutive sessions. Without the exposure clause a goal is met by
never practicing the thing, which is the same trap `reading-the-profile.md`
warns about, promoted into an objective.

## Three or four active goals, no more

Only the first focus goal is read by the picker, so a fifth focus goal is
decoration. More practically, goals are a commitment about where limited
practice time goes, and a list of eight is a list of none. A good set is usually
one focus goal, one vocab goal, and one habit goal — they draw on different
things and don't compete.

## Reviewing, and closing goals honestly

At each review, append a checkpoint to every active goal and set its status:

- **met** — the `measure` is satisfied. Move it to `archive` and say what
  replaced it. Archived goals are the most encouraging thing in the file and the
  only durable record that something got fixed, since `weaknesses` never shows
  it.
- **stalled** — several reviews, no movement, and drilling has happened. Do not
  quietly re-set the same goal with a later date; that is how a goal becomes
  wallpaper. Say what will change, or drop it.
- **dropped** — no longer worth the time. A legitimate outcome, and better
  recorded than left rotting in `active`.

A goal still `active` after three reviews with no checkpoint movement is a goal
nobody believes in.

## Level goals

`profile["level"]` is inert — no code writes it. Changing it changes the
difficulty of every future lesson, so:

1. Set `level_target` first and leave `level` alone.
2. Judge readiness against `language-lesson/references/levels.md`, which is the
   single source of truth for what a band means. Don't restate its criteria in a
   goal; reference it.
3. Confirm across at least two reviews before writing the new `level`.

Bumping a level early is a quiet failure mode. The student bounces off material
they cannot read, the profile records more weaknesses rather than fewer, and
nothing in the data says "this text was too hard" — it just looks like they got
worse.
