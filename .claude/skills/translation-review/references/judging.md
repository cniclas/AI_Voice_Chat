# Judging a spoken translation (language-neutral)

The input is a transcript of someone talking. Between the student's understanding
and the text you are reading sit two lossy layers: their speech, and Whisper.
Most of what looks wrong in a transcript was introduced by those layers rather
than by a misunderstanding, and a review that ignores this tells students they
made mistakes they did not make. That erodes trust in the tool faster than
missing a real error does.

## The discriminator that works: repetition

A single deviation is noise. The same deviation three times is a finding.

This is more reliable than any phonetic reasoning, because the failure modes have
different statistics. Whisper's errors are scattered — it mishears a word here,
a name there, driven by audio conditions that vary sentence to sentence. A
learner's errors are systematic — someone who reads `le` as a subject does it
every time `le` appears, because they hold a wrong rule.

So when you spot something, look for its siblings before deciding. Three
sentences where an indirect object became the subject is a rule the student is
missing. One is a mumble.

## Never flag these

Spoken input has no spelling, so any error that exists only in orthography is an
artifact of transcription and cannot be attributed to the student:

- **Spelling and punctuation of every kind.** They did not spell anything.
- **Homophones** — their/there/they're, its/it's, to/two/too. The student's mouth
  produced one sound; the spelling is Whisper's guess.
- **Capitalization**, including names read as common nouns.
- **Sentence boundaries.** Whisper punctuates by prosody, so run-ons and splits
  reflect where they breathed.

Ordinary speech behavior is equally out of bounds:

- **Disfluency and filler** — "um", "like", "I mean", repeated words at a
  restart.
- **Self-correction.** "He went— he was going to the market" is the student
  *fixing* it. Judge the final version; a visible repair is evidence of
  understanding, not against it.
- **Dropped function words at speed** — a missing "the" or "a" in fluent speech
  is articulation, not grammar, especially in the native language.
- **Trailing off** at the end of a long sentence.
- **Hallucinated repetition** around pauses, a known artifact when Whisper hits
  silence or breath.

## Signals it is the learner, not the microphone

- **The wrong word is semantically wrong but phonetically far** from the right
  one. Hearing "road" for "river" is a microphone; saying "road" where the
  Spanish says *río* and nothing sounds like "road" is a lookup failure.
- **The deviation tracks a structure in the source.** A pronoun that matches the
  wrong participant, a tense that matches a different verb form on the page — the
  error has a *shape* that maps onto the grammar. Whisper knows nothing about the
  Spanish and cannot produce a mistake shaped like its morphology.
- **A whole clause is missing.** Whisper drops words; it rarely drops a complete
  proposition.
- **A false friend rendered as its English lookalike.** This is close to
  diagnostic, since it is precisely what a learner does and has no acoustic
  explanation.
- **Hedging in the audio itself** — long pauses, a rising uncertain tone,
  "something like". If you have the audio, that hesitation marks where they were
  guessing.

## Paraphrase is not error

The student is translating, not producing a key. Any English that carries the
same meaning is correct, and treating one target rendering as the only right
answer teaches them to translate word-for-word — the opposite of the goal.

Correct, all of them: a different register ("got up" / "rose"), a restructured
sentence, an active-to-passive shift that keeps the participants straight, an
idiom rendered by a different idiom. Only flag where the *meaning* moves.

The exception is when the lesson was drilling a structure and the paraphrase
routes around it. That is not an error either — it is a **Check**, below.

## The three verdicts

**Missed.** The meaning is wrong: a participant swapped, a negation lost, a
lexical item confused, a clause invented. These are the findings that matter
most and they are usually unambiguous once you have filtered for noise.

**Blurred.** The meaning survives but a distinction the lesson was built around
has been flattened. The English sounds fine, which is why this category gets
missed. A student translating an aspect contrast into undifferentiated simple
past has produced acceptable English and shown you nothing about whether they saw
the contrast — worth naming, gently, because it is the whole point of the lesson.

**Check.** You genuinely cannot tell. English does not mark much of what other
languages mark: mood, formality, aspect in many contexts, the ser/estar split.
When the student's rendering is compatible with both readings, do not guess.

Guessing here is the most damaging failure this skill can make, because it
produces a confident correction of something that was never wrong. Ask instead —
one short question aimed exactly at the ambiguity ("was that a habit or a single
occasion?"). The answer takes them five seconds and settles it honestly.

Two or three check questions is useful. Ten is an interrogation; pick the ones
that sit on the lesson's focus.

## Weighting by lesson focus

A lesson drills something specific. Errors on the target structure are the
review's reason for existing — report them fully, with the rule. Errors elsewhere
are worth a line if the meaning broke, and worth nothing if it didn't.

This keeps reviews short and pointed. A student who misreads one incidental
vocabulary item in a subjunctive lesson does not need a paragraph about it.

## Don't manufacture findings

If the translation is good, the review is short. Say what held up, ask a check
question if one is genuinely open, and stop.

The temptation runs the other way — a review with three findings feels more
useful than one with none. It isn't. A student who is told their correct
translation was wrong learns to distrust either the tool or their own accurate
instincts, and both outcomes are worse than a two-line review saying they nailed
it.
