# Reading the student profile

What each field in `recordings/<user>/student_profile.json` records, how it gets
written, and — the part that matters — what it can and cannot prove.

## The shape

```json
{
  "version": 1,
  "level": "B1",
  "articles_covered": [{"title": "...", "date": "...", "session": "..."}],
  "weaknesses": [{"type": "grammar", "topic": "...", "explanation": "...",
                  "occurrences": 4, "first_seen": "...", "last_seen": "..."}],
  "vocab_to_practice": [{"word": "...", "times_targeted": 0, "last_seen": "..."}],
  "practice_log": [{"date": "...", "session": "...", "mode": "challenge",
                    "focus": "...", "level": "B1", "topic": "...",
                    "translated": true, "spoken_turns": 11}],
  "focuses_practiced": [{"focus": "...", "times": 4, "last_practiced": "..."}],
  "goals": {"active": [], "archive": []}
}
```

`weaknesses` and `vocab_to_practice` are written by the analysis pass
(`curriculum.merge_analysis_into_profile()`); `practice_log`, `focuses_practiced`
and `articles_covered` by `record_practice()` and `record_article_covered()`;
`goals` only by this skill. `level` by nothing.

## The counts only go up

`merge_analysis_into_profile()` does exactly two things to a weakness it sees
again: `occurrences += 1` and `last_seen = today`. There is no decrement, no
"resolved" flag, no expiry. New weaknesses enter at `occurrences: 1`.

So `occurrences` is a **lifetime total, not a current severity**. Ranking
weaknesses by it — which is what `curriculum.top_weaknesses()` does, correctly,
for its own purpose of picking something to drill — tells you what has gone wrong
most over all time, which for a long-running student is mostly a record of what
they were bad at when they started.

Everything below is about recovering a current signal from that.

## Recency against exposure

The one computation this skill is built on. For each weakness, two numbers:

- **Sessions since `last_seen`** — how long it has been quiet.
- **Exposing sessions since `last_seen`** — how many of those sessions could
  plausibly have surfaced it.

A session exposes a weakness if its `practice_log` entry drilled a related
`focus`, or — weaker but real — if it was a conversation long enough
(`spoken_turns`) that the structure would likely have come up. Grammar the
student can simply avoid (subjunctive, conditionals) needs a targeted focus to
count as exposure; things they cannot avoid in any sentence (agreement, article
choice, past-tense aspect in any narration) are exposed by any real conversation.
Which is which depends on the language: a focus pattern whose entry says "force
into the story" is by definition one the student would otherwise dodge.

That yields three states the raw count collapses into one:

| Exposing sessions since | Still recurring? | Read as |
|---|---|---|
| Several | No | **Moving** — real evidence of learning |
| Several | Yes | **Stuck** — drilling is not working, change approach |
| None | No | **Not yet tested** — no information either way |

**Worked example.** `object pronouns and clitics`, `occurrences: 8`,
`first_seen: 2026-02-11`, `last_seen: 2026-05-03`. Since May 3rd there have been
nine sessions, six of them conversations of 10+ turns and one a challenge focused
on `Object pronouns and clitics`. Eight occurrences looks alarming and is the
top row of the table; the interpretation is that this student *had* a clitic
problem and no longer does. Report it as resolved, and consider proposing the
goal be archived as met.

Change one fact — those nine sessions all drilled `Ser vs estar` and ran three
turns each — and the same row means nothing at all. Same count, same dates,
opposite conclusion. This is why the exposure column is not optional.

## Stuck is a different finding from failing

A weakness recurring in a student who has never drilled it needs practice. A
weakness recurring in a student who has drilled it four times needs *something
else*: a different pairing (the language-lesson skill notes that two focuses
which interact naturally teach more than either alone), a translation challenge
instead of conversation so the structure has to be decoded rather than avoided,
or a level drop for that one structure so the surrounding text stops competing
for attention.

"Keep practicing" is not a finding. If `focuses_practiced` shows the focus at
three or more and `last_seen` is recent, the honest report is that the current
approach is not landing.

## Blind spots: the focus-versus-weakness join

Cross `weaknesses[].topic` against `focuses_practiced[].focus`. A topic with high
occurrences that has *never* appeared as a practiced focus is usually the single
most actionable line in a review.

This happens structurally, not by accident. `skill_refs.pick_focus()` matches
weakness topics against the focus patterns documented in the student's
target-language file, `language-lesson/references/languages/<target>.md`, by
token stem. A weakness whose
topic the analysis pass phrased in a way that doesn't stem-match any of those
names — "using ser for temporary states", say, rather than "ser vs estar" — is
invisible to the picker forever. It accumulates occurrences and never gets
chosen.

Naming it as a goal is the fix, since a focus goal is matched exactly rather than
by stem.

## What `times_targeted: 0` means

`bump_vocab_targeted()` only increments a word when it was actually woven into a
generated story. A word sitting at `times_targeted: 0` with an old `last_seen`
is one the pipeline flagged as needed and has never once acted on — usually
because `top_vocab_to_practice()` returns five words and this one is below the
cut every time.

That is a real gap and a cheap fix: a vocabulary goal promotes those words ahead
of the queue.

## Truncation ceilings distort history

Every list is capped, and things fall off the bottom silently:

| List | Cap | Kept by |
|---|---|---|
| `weaknesses` | 30 | highest `occurrences` |
| `practice_log` | 60 | most recent |
| `articles_covered` | 60 | most recent |
| `vocab_to_practice` | 40 | insertion order |

The dangerous one is `weaknesses[:30]`, sorted by occurrences before slicing. A
low-count weakness pushed off the end has **disappeared, not been resolved**, and
from the outside the two look identical. If the list is at 30, say so in the
report rather than treating absence as evidence.

`practice_log[-60:]` bounds every "sessions since" number you can compute. If the
log is at 60 entries, dates before the oldest entry are unknowable, and a
`first_seen` older than that cannot be turned into a rate.

## Cadence and depth

From `practice_log`:

- **Gaps between `date`s** — the strongest predictor of whether anything sticks,
  and the one thing in the profile the student controls directly.
- **`spoken_turns`** — a session of three turns and one of twenty are both one
  row. Depth matters for whether a session counts as exposure.
- **`mode` and `translated`** — whether challenge mode is being used at all.
  A student who only ever free-converses is never having their reading tested,
  which means half the weaknesses in the profile come from one channel.
- **`focus` distribution** — heavy repetition of one focus alongside untouched
  others is what the blind-spot join formalizes.

## `level` is inert

Only ever read (`translation_challenge.build_lesson()`, and the graphs that log
it). The sole write in the codebase is the `"B1"` default in `_empty_profile()`.
Nothing observes the student and moves it.

So a student can practice for a year at the level they were assigned on day one.
Raising the question is this skill's job; `language-lesson/references/levels.md`
holds the criteria for answering it.
