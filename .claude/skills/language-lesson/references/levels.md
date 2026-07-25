# CEFR level bands (language-neutral)

These bands describe what a reader at each level can *do* and how much text they
can take. They hold for any target language.

What they deliberately do not say is which grammatical forms belong at each
level — that mapping is language-specific and lives in
`references/languages/<code>.md`. The division matters: "can narrate past events
with some texture" is a fact about B1 readers everywhere, while the forms that
deliver it are the Spanish imperfect, the German Präteritum, or the Japanese
だった, and each language draws its own line about which of those arrives when.

Read the band for the requested level, then read the same level's row in the
language file to turn it into forms.

## Measuring text budget across languages

Word counts assume a space-separated language of roughly English or Spanish
density. Adjust where typology makes words a bad unit: German compounding
inflates word length while deflating word count, and Japanese or Chinese need
characters or clauses instead.

The portable measure is **clauses per sentence**, given alongside word counts
below. When the two disagree, trust the clause count.

## A1 — present, concrete, immediate

- **Can do:** follow statements about immediate surroundings, routine, and
  personal facts. Needs every referent visible or already named.
- **Budget:** 60-100 words. One clause per sentence, occasionally two joined by
  a coordinator. Under 8 words per sentence.
- **Functions in play:** identifying, describing, stating possession and
  location, expressing likes, naming a routine, saying what happens next.
- **Out of scope:** any displacement in time beyond an immediate plan; anything
  hypothetical, reported, or conditional; multi-clause subordination.
- **Story shape:** a person, a place, a habit, a small change. Plot is almost
  beside the point at this level — the reward is understanding whole sentences
  unaided.

## A2 — past and future arrive, one at a time

- **Can do:** follow a simple sequence of events in the past and simple plans.
  Handles a second clause if the connector is transparent.
- **Budget:** 100-150 words. Two clauses per sentence. Under 12 words.
- **Functions in play:** narrating a completed sequence, describing past states,
  expressing obligation and intention, comparing, referring back to something
  already mentioned with a pronoun.
- **Out of scope:** hypotheticals, irrealis or unasserted mood, reported speech
  with tense shifting, aspectual contrast within a single sentence.
- **Story shape:** something happened, in order, to someone concrete.

## B1 — narration with texture

The repo's default, and what `curriculum.py` generates for sessions.

- **Can do:** follow a narrative where background and event are distinguished,
  and where the writer's attitude — wanting, doubting, planning — colors the
  telling.
- **Budget:** 150-220 words. Two or three clauses per sentence, including one
  layer of subordination. Under 18 words.
- **Functions in play:** contrasting background against event, expressing
  wishes, doubts, and unrealized situations, referring to a future seen from the
  past, giving reasons and purposes, relative modification.
- **Out of scope:** counterfactuals about the past, dense literary register,
  subordination two layers deep.
- **Story shape:** someone remembers, or discovers that what they believed was
  wrong. This is where a story can have a turn.

## B2 — hypotheticals and argument

- **Can do:** follow reasoning, concession, and speculation. Tolerates a
  sentence that has to be held in mind to the end.
- **Budget:** 220-300 words. Three or more clauses; 25-word sentences are fine
  when the structure earns them.
- **Functions in play:** hypothetical and counterfactual reasoning, concession
  and contrast, impersonal and agentless statement, reported speech with proper
  shifting, near-synonym choice carrying connotation.
- **Story shape:** a decision with a cost, or a version of events that turns out
  to be one perspective among several.

## C1 — nuance and effect

- **Can do:** read for how something is said, not only what. Catches irony,
  implicature, and register shift.
- **Budget:** 300-400 words. Periodic sentences; length limited by rhetoric
  rather than rule.
- **Functions in play:** the full irrealis range including regret about the
  past, marked word order for emphasis, figurative and idiomatic language,
  register as characterization.
- **Story shape:** the telling itself carries meaning the events do not.

## C2 — near-native

- **Budget:** 350-450 words.
- Wordplay, ambiguity held deliberately open, register shifting inside a
  paragraph, cultural and literary allusion.
- The literal gloss is at its most valuable here, because the distance between
  the literal and the natural line is exactly where the style lives.

## How the translation changes across levels

The natural English line does not change. It stays ordinary and idiomatic at
every level — a C1 story does not get a fancier English rendering, it gets one
that sits further from the literal line.

The literal line gets busier as the level rises: more bracketed material, more
grammar tags, more hyphenated compounds where one word in the target language
needs three in English. That widening gap is itself the lesson, and it is why
the two lines are worth keeping separate rather than compromising on one
middling translation.

## Sanity check before finishing

Could a learner at this level read the story once, aloud, and follow it without
stopping? If any sentence would need a reread to parse, that sentence belongs a
level up. This catches drift better than counting forms does.
