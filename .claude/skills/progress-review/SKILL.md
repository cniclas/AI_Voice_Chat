---
name: progress-review
description: Review a language student's long-term progress across all their recorded sessions and set or revise concrete practice goals. Reads the accumulated student profile — recurring weaknesses, what has actually been drilled, session cadence, vocabulary coverage — and reports honestly on what is improving, what is stuck, and what has never been tested, then writes measurable goals back into the profile so the app's generated lessons target them. Use this skill whenever the user asks how they are doing, whether they are getting better, what they should focus on next, what they have been neglecting, whether they are ready to move up a CEFR level, or wants goals set, reviewed, or adjusted. Trigger it for phrasings like "am I improving", "how's my Spanish going", "what should I work on this month", "give me a goal", "review my progress", "am I ready for B2", or "what am I still bad at" — even when no skill is named and no file is handed over.
---

# Progress review

The other two skills work a single session: `language-lesson` writes today's
material, `translation-review` marks today's attempt. This one reads the whole
record and answers the question neither of them can — *is this actually
working?* — then turns the answer into goals the app will act on.

## What you are reading, and what it can prove

Everything comes from `recordings/<user>/student_profile.json`, which the
session pipeline appends to after every session. Before interpreting a single
number, understand its one structural quirk:

**The profile only ever counts upward.** `curriculum.merge_analysis_into_profile()`
increments `occurrences` and refreshes `last_seen`. Nothing anywhere decrements
a count or marks a weakness resolved. So a topic sitting at nine occurrences is,
from the count alone, indistinguishable between "failing this every session" and
"fixed back in April".

This is the whole reason the skill exists as more than a printout. Progress is
legible here only as **recency measured against exposure** — did the student get
chances to make this mistake, and did they stop making it? A reviewer who reads
the counts as a leaderboard will confidently report the exact opposite of the
truth, congratulating the student on a weakness they have simply never been
tested on.

`references/reading-the-profile.md` is that reasoning in full. It is the
substance of this skill; read it before interpreting anything.

## Inputs

Before anything else: confirm which student this is for — Niclas or Alejandra —
if the conversation hasn't already settled it. It decides the `<user>` segment in
every path below, and reviewing the wrong profile wastes the whole exercise.

| Input | How to resolve it if not given |
|---|---|
| **Student** | Required. Ask; there is no sensible default between two people. |
| **Window** | Since the last review in `recordings/<user>/progress/`, if one exists. Otherwise the whole `practice_log`. Say which you used. |
| **Goals: report or revise?** | Default to both — report first, then propose changes. If they only asked "how am I doing", still show goal status, but don't rewrite goals without saying so. |

## Step 1 — take the snapshot

```bash
uv run python .claude/skills/progress-review/scripts/progress_snapshot.py --user niclas
```

Add `--since 2026-05-01` to bound the window, `--json` for the structured form
when you want to quote exact figures.

Use the script rather than reading the JSON and tallying by eye. The arithmetic
— sessions since a date, which sessions exposed which weakness, the focus-versus-
weakness join — is deterministic and completely uninteresting, and doing it
mentally across sixty log entries is where a review quietly becomes wrong. The
script also reports which truncation ceilings are currently binding, which you
cannot see by reading the file.

## Step 2 — interpret

Read `references/reading-the-profile.md`, then sort every weakness into one of
four states. The distinction between the last two is the one that matters most:

- **Moving** — not recurring, and there were sessions since that would have
  exposed it. Evidence of learning.
- **Stuck** — still recurring despite repeated drilling. The signal is
  `focuses_practiced` showing the focus at three or four times with `last_seen`
  still recent. More of the same is not the answer; say what to change.
- **Not yet tested** — not recurring, but nothing since would have exposed it.
  This is *not* progress, and reporting it as progress is the single most likely
  way to mislead the student.
- **Blind spot** — high occurrences, never practiced as a focus at all. Usually
  the most actionable thing in the whole profile.

## Step 3 — write the report

Save to `recordings/<user>/progress/<YYYY-MM-DD>.md`, creating the directory if
needed. It sits under `recordings/`, so it is gitignored along with everything
else personal.

Past reports are kept rather than overwritten, and it is worth opening the last
one before writing this one: what you predicted last time and what actually
happened is itself the most honest measure of whether the practice is working.

```markdown
# Progress review — <user> — <YYYY-MM-DD>

**Window:** <from> → <to> · **Sessions:** <n> · **Level:** <level>

## Where you are

<Two or three sentences. Specific and falsifiable — "you have stopped losing the
imperfect in narration, but object pronouns are still going wrong every session",
not "good progress overall".>

## Moving

<Weaknesses that have gone quiet despite exposure. Give the evidence: occurrences,
last seen, and how many exposing sessions have run since.>

## Stuck

<Recurring despite drilling. Each one needs a *different* suggestion, not "keep
practicing" — a different focus pairing, a translation challenge instead of
conversation, a level drop for that structure.>

## Not yet tested

<Logged, never exposed since. Explicitly not progress.>

## Blind spots

<High-occurrence topics that have never been a practiced focus.>

## Goals

| Goal | Set | Target | Baseline | Now | Status |
|---|---|---|---|---|---|

## Next

<Two or three concrete things, each tied to a goal.>
```

## Step 4 — set or revise the goals

Read `references/goals.md` for what makes a goal workable here and for the exact
schema. Two things decide most of it: a goal has to be measurable against fields
the pipeline actually writes, and a focus goal has to name a canonical focus from
`language-lesson/references/languages/es.md` or the app will silently ignore it.

Propose the goals in the conversation and get agreement before writing. These
change what the app generates tomorrow, so they are not yours to set unilaterally.
Then:

```bash
uv run python .claude/skills/progress-review/scripts/record_goals.py goals.json --user niclas --dry-run
uv run python .claude/skills/progress-review/scripts/record_goals.py goals.json --user niclas
```

The script validates before writing and prints what changed. It rejects a focus
goal naming a focus the language file doesn't document — that mistake is
invisible otherwise, and the goal would simply never influence a lesson.

Tell the user in one line what landed. This is shared state: `skill_refs.pick_focus()`
will hand the goal's focus to tomorrow's lesson, `curriculum.top_vocab_to_practice()`
will weave goal words into the story, and the tutor's system prompt will steer
conversation toward the structure.

## Step 5 — the level question

`profile["level"]` is never written by any code path in this repo — it is only
ever read, by the lesson builder deciding how hard to make today's story. This
skill is the only thing that moves it, so if nobody raises the question it never
gets raised.

Judge readiness against `.claude/skills/language-lesson/references/levels.md`,
which is the single source of truth for what each band means; don't restate the
criteria here or the two will drift. The practical bar on top of it: a level bump
raises the difficulty of *every* future lesson, so it should follow sustained
evidence across several sessions, never one good day. A student who is bouncing
off their material learns nothing, and the profile has no way to tell you that is
happening — they will just stop practicing.

If a bump looks right, set `level_target` as a goal first and let the next review
confirm it before changing `level` itself.

## Don't manufacture a trajectory

A student who practiced twice this month has a two-line report, and writing it
honestly is a real result. Four data points do not contain a trend, and inventing
one teaches them to distrust the next review — or worse, to stop working on
something that was never actually fixed.

When the data is too thin, say so plainly, say what would make the next review
meaningful ("four more sessions, at least two of them translation challenges"),
and stop. `translation-review` takes the same line on findings for the same
reason: a review that always produces something impressive stops being
information.
