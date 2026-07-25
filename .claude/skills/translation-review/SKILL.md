---
name: translation-review
description: Review a student's spoken translation of a foreign-language text back into their native language — they read a Spanish story and say the English aloud, Whisper transcribes it, and this skill compares it against the source to find genuine comprehension errors, separate them from speech-recognition noise, and explain each one with a correction. Pairs with the language-lesson skill, using that lesson's literal gloss as the answer key. Use this skill whenever the user has spoken or recorded a translation and wants it checked, asks "did I get this right", hands over a WAV or transcript of themselves translating, wants to know which words or tenses they misread, or wants their translation graded, marked, or corrected. Trigger it even when they just paste a transcript alongside a Spanish text without naming the skill.
---

# Translation review

The student reads a Spanish story and speaks its English meaning aloud. Whisper
transcribes that. This skill turns the transcript into a short, honest review:
what they misunderstood, why, and what the Spanish actually said.

## What is actually under test

They are translating *into* their native language. That single fact settles most
judgment calls in this skill.

Their English is not the subject — it is the readout. Awkward English, a dropped
article, an odd word choice: none of that tells you anything about their Spanish,
because English is the language they already have. What the English reveals is
whether they **decoded the Spanish correctly**. A wrong pronoun means they misread
a clitic. A flattened tense means they missed an aspect contrast. A missing clause
means they skipped a line or guessed.

So: read every deviation as a question about the *Spanish*. If a deviation has no
plausible Spanish explanation, it is almost certainly the microphone or ordinary
speech, and it is not a finding.

## Inputs

| Input | Notes |
|---|---|
| **The spoken translation** | A transcript, or a WAV to transcribe with `scripts/transcribe.py`. Required. |
| **The source text** | Best: the lesson file from the `language-lesson` skill, which carries an answer key. Otherwise raw Spanish text — then work out the reference translation yourself before reading their attempt, so their wording doesn't anchor you. |
| **The lesson focus** | From the lesson file's header. It decides which distinctions matter most; without it, weight all equally. |

### Why the lesson file is worth asking for

A lesson from `language-lesson` gives you two things a bare Spanish text can't.
The `Nat.` line is the target — what a competent translation looks like. The
`Lit.` line is the diagnostic instrument: when the student's English diverges,
the gloss shows you *which word or morpheme* they lost, so the explanation can
point at the exact thing rather than paraphrasing the whole sentence.

If the student translated something with no lesson file, the review still works;
you just have to build the reference yourself.

## Step 1 — get the transcript

```bash
uv run python .claude/skills/translation-review/scripts/transcribe.py <audio.wav>
```

Defaults to `large-v3`, the model the app loads, so the transcript matches what
the rest of the pipeline would produce. Pass `--model base` if you only need a
rough pass and the load time is hurting. Transcribe with `--language en`, the
default — the student is speaking English, and letting Whisper auto-detect
invites it to hear Spanish in the proper nouns.

## Step 2 — align transcript to source sentences

Students read in order, so alignment is mostly positional: match on content
words, sentence by sentence. Three things to watch for, none of which are errors
in themselves:

- **Merged sentences.** Two Spanish sentences rendered as one English sentence is
  a legitimate translation choice.
- **Skipped sentences.** A whole sentence missing *is* data — silence usually
  means it was hard. Note it and ask, rather than scoring it as wrong.
- **A summary instead of a translation.** "Basically the guy woke up and it was
  cold" is not a translation, and a summary hides exactly the morphology under
  test. Say plainly which sentences you could not assess from it and offer to
  review a closer reading.

If the student read the *literal gloss* aloud rather than natural English, that is
decoding, not an error. Say so, and nudge them toward rendering the meaning once
they have parsed it — the two lines exist for two different stages.

## Step 3 — judge, through two filters

Nothing becomes a finding until it survives both. Read `references/judging.md`
before this step; it is the substance of the skill.

1. **Microphone or learner?** Whisper mishears. Spoken English has no spelling,
   so orthography, punctuation, homophones, and phonetically-near substitutions
   are unfalsifiable and must never be flagged. The single most reliable
   discriminator: a one-off deviation is noise, the *same* deviation three times
   is a finding.
2. **Different meaning, or just different words?** "He rose early" for "he got up
   early" is correct. Flag only where the meaning diverges or a grammatical
   distinction the lesson was drilling got lost.

Then read `references/languages/es.md` for the traps specific to reading Spanish
into English — false friends, clitic misreadings, mood and aspect flattening —
each with the shape of the mistake as it appears in an English transcript.

### The three verdicts

- **Missed** — the meaning is wrong. They read something the Spanish does not say.
- **Blurred** — the meaning is roughly right but a distinction the lesson was
  drilling has vanished. This is the most valuable category in a focused lesson
  and the easiest to overlook, because the English usually sounds fine.
- **Check** — you cannot tell from the English whether they understood, because
  English does not mark what Spanish marks.

That third verdict matters more than it looks. If a lesson drills preterite
against imperfect and the student says "he told me stories", that is a perfectly
good rendering of *contaba* — and also of *contó*. You cannot know which they
read. Guessing produces a false accusation; skipping it wastes the most
diagnostic moment in the lesson. So convert it into a question: *"In sentence 1,
was that a one-time thing or something that happened repeatedly?"* One question,
aimed at the ambiguity, answers it honestly.

### Do not manufacture findings

A good translation reviewed honestly produces a short review, and that is a real
result — the app already treats "no notable errors" as a valid analysis outcome.
Inventing borderline nitpicks to look thorough teaches the student that their
correct work was wrong, which is worse than saying nothing.

## Step 4 — write the review

```markdown
# Translation review — <lesson title>

<One or two sentences: what they clearly understood, and the through-line of
what they didn't. Specific, not encouraging-noise.>

## What to fix

**Sentence 4 — missed**
> You said: "he sold the house to his brother"
> Spanish: *Le vendió la casa a su hermano.*
> Literal: *to-him [he]-sold the house to his brother*

<What went wrong, in one or two sentences, pointing at the specific word. Then
the rule it illustrates, and the corrected rendering.>

**Sentence 7 — blurred**
> ...

## Worth checking

**Sentence 1** — <the question that resolves the ambiguity>

## Holding up well

<Two or three things they got right that were not easy — specific sentences, not
generic praise. This is not padding: it tells them which of their instincts to
trust.>
```

Order findings by what will help most, not by sentence number. If one
misunderstanding recurs across sentences, treat it as a single finding with
several pieces of evidence — that is one thing to learn, not four.

Save alongside the lesson: `lessons/<lesson-slug>-review-<YYYY-MM-DD>.md`.

## Step 5 — record it in the student profile

The findings belong in the same profile the live sessions write to, so that
tomorrow's story weaves in what today's translation exposed. Write the findings
as JSON in the shape `curriculum.analyze_weaknesses()` produces, then:

```bash
uv run python .claude/skills/translation-review/scripts/record_review.py review.json
```

The script reuses `curriculum.merge_analysis_into_profile()` rather than editing
the JSON by hand, so recurring weaknesses accumulate `occurrences` exactly the
way the session pipeline counts them. Pass `--dry-run` to see the effect first.
Tell the user in one line what got recorded — this is shared state the rest of
the app reads.

Skip this step when the review found nothing worth recording. An empty merge is
harmless but it pads the profile with noise.

## A note on the other direction

Reviewing the student *speaking Spanish* is a different job, and the app already
does it — `session_graphs.py` runs `analyze_weaknesses()` over the conversation
transcript at the end of every session. This skill is specifically for
comprehension checked through translation into English, which tests something
that conversation does not: whether they can read closely, rather than infer from
context and keep talking.
