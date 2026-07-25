---
name: language-lesson
description: Build a graded Spanish lesson from a piece of source material — a short story, an article, a paragraph of facts, a Wikipedia extract — targeting a specific grammar focus (past tenses, conditional, subjunctive, ser/estar, por/para, or any combination) at a chosen CEFR level (A1-C2), and pair it with a word-for-word literal English translation so every single word and phrase can be traced back. Use this skill whenever the user wants a Spanish story, reading, text, or lesson written for practice, wants an existing text rewritten or re-graded to a level, wants to drill a tense or grammar point through reading, or asks for an interlinear/literal/word-for-word translation alongside a natural one. Trigger it even when the user just pastes some text and says something like "make me a B1 lesson about this focusing on the past tenses" without using the word "skill" or "lesson".
---

# Language lesson builder

Produce a lesson with two halves that must stay in lockstep: a Spanish story
written to a level and a grammar focus, and an aligned translation that lets
the learner interrogate any word in it.

The reason the translation matters so much: this repo is a speaking tutor, and
a learner reading alone hits a word, wonders "why is it *le* and not *lo*", and
has nobody to ask. A normal translation can't answer that — it dissolves the
Spanish into fluent English and the mapping is gone. So the lesson carries a
*literal* line that preserves Spanish word order and morphology, plus a natural
line so the meaning still lands. The learner can then point at any word and the
answer is already on the page.

## Inputs

Five things shape a lesson. Only the first is essential — fill in the rest from
context, state what you assumed in one line, and get on with writing:

| Input | How to resolve it if not given |
|---|---|
| **Source context** — the story, facts, or article to build from | Required. If genuinely absent, ask for it; everything else is guessable, this isn't. |
| **Focus** — the grammar being drilled | Read the source: a historical account wants past tenses, a plan or forecast wants future/conditional. Propose one and say so. Or pull the top recurring weakness from the student profile. |
| **Level** — A1-C2 | `recordings/student_profile.json` → `"level"`. Falls back to B1, which is what this repo assumes. |
| **Theme / tone** — cozy, mysterious, comic, documentary | Optional. Default to warm and concrete; the app reads these aloud to a casual learner, so avoid violence, disaster, and death even when the source is full of them. |
| **Languages** | Target Spanish, native English, unless told otherwise. The glossing conventions below generalize to other pairs. |

Before writing, read `references/cefr-es.md` for the row matching the level, and
the matching section of `references/focus-patterns.md` for the focus. They carry
the calibration detail — length, permitted tenses, what to contrast, what
English speakers get wrong — that keeps output from drifting a level or two
upward, which is the usual failure mode when writing "B1 Spanish" from feel.

If `recordings/student_profile.json` exists, also pull `vocab_to_practice` and
the top `weaknesses`; weaving a few of those words into the story costs nothing
and makes the lesson land on what this particular student keeps missing.

## Writing the story

Build a *new* semi-fictional story from the source rather than summarizing it.
Invent people and detail, keep the real subject matter recognizable. The story
exists to carry the grammar focus, so plot it around situations that demand the
target structures — a lesson on past tenses needs a narrator remembering
something, a lesson on conditionals needs a choice or a hypothetical.

Two constraints come from the app itself:

- **It gets read aloud** by Kokoro TTS (`kokoro/tts.py`). Plain prose paragraphs
  only — no headings inside the story, no lists, no parentheses, no quotation
  marks beyond ordinary dialogue, no digits or symbols that a synthesizer will
  read out awkwardly. Write "mil novecientos veinte", not "1920".
- **It gets discussed afterwards**, so it needs something to talk about: a
  person with a motive, a small turn of events, a question left open.

Density matters more than volume. A learner gets more from eight sentences that
put the target structures side by side than from twenty that use them once
each — see the focus reference for what "contrast" means for each grammar point.

## The aligned translation

This is the part worth slowing down for. Number every sentence of the story and
give each one two lines beneath it.

**The literal line** maps Spanish to English word by word, in Spanish order. It
is not English and should not read like English. If it reads smoothly you have
almost certainly smoothed away the thing the learner needed to see.

Notation, kept deliberately small so it stays readable:

- `-` joins English words that translate a single Spanish word: `contaba` →
  `told` or `used-to-tell`; `del` → `of-the`.
- `[ ]` supplies meaning that Spanish encodes without a separate word — dropped
  subjects, the implied "there" of *hay*: `Fue al mercado.` →
  `[He] went to-the market.`
- `/` offers alternatives where the form is genuinely ambiguous out of context:
  `le` → `to-him/her`.
- Grammar tags like `(subj)`, `(impf)`, `(pret)` go in **only when the focus
  turns on that distinction**. On a past-tense lesson, marking imperfect vs
  preterite is the whole point; on a vocabulary lesson it is noise.
- Punctuation mirrors the Spanish, including `¿` and `¡`.

**The natural line** is ordinary, idiomatic English — what a translator would
actually write. It exists so the learner can check comprehension after decoding.

Worked examples:

> **1.** Cuando era niño, mi abuelo me contaba historias del río.
> - *Lit.* When [I]-was(impf) boy, my grandfather to-me told(impf) stories of-the river.
> - *Nat.* When I was a boy, my grandfather used to tell me stories about the river.

> **2.** Se levantó temprano porque hacía frío.
> - *Lit.* Himself [he]-raised(pret) early because [it]-made(impf) cold.
> - *Nat.* He got up early because it was cold.

> **3.** Si tuviera tiempo, le escribiría una carta.
> - *Lit.* If [I]-had(impf-subj) time, to-him/her [I]-would-write a letter.
> - *Nat.* If I had time, I'd write him a letter.

Notice what the literal line does in example 2: `hacía frío` becomes
"[it]-made cold", not "it was cold". Spanish expresses weather with *hacer*, and
a learner who never sees that will keep saying *era frío*. Idioms are exactly
where the literal line earns its place — keep it literal, let the natural line
fix it, and flag it in the vocabulary table if it is likely to trip someone up.

## Focus notes and vocabulary

After the aligned reading, explain the grammar in English using sentences pulled
from *this* story rather than invented textbook examples — the learner has just
met them in context, which is the moment the rule sticks. Three or four points,
each with the example, the rule in one line, and where an English speaker
typically goes wrong.

Then a vocabulary table for words at or above the level, and any idiom whose
literal line looked strange. Skip the obvious cognates.

Close with a few speaking prompts in Spanish that can't be answered without
using the target structure. They feed straight into the app's conversation
phase, which is where the reading turns into production.

## Output template

```markdown
# <Título en español>

**Nivel:** B1 · **Enfoque:** pretérito vs imperfecto · **Tema:** <theme>
**Source:** <what the lesson was built from, one line>

## Historia

<plain Spanish prose, unnumbered, TTS-safe>

## Lectura alineada

**1.** <Spanish sentence>
- *Lit.* <word-for-word gloss>
- *Nat.* <idiomatic English>

**2.** ...

## Notas de gramática — <focus>

**<point>** — *<example from the story>*
<one-line rule, then the typical English-speaker mistake>

## Vocabulario

| Español | Literal | English | Nota |
|---|---|---|---|

## Para hablar

1. <question in Spanish that forces the target structure>
```

The story appears twice — once as unbroken prose, once split across the aligned
reading. That is intentional: the prose version is what gets read aloud and read
for pleasure, and breaking it into numbered fragments would ruin both.

## Saving the lesson

Write to `lessons/<YYYY-MM-DD>-<slug>.md`, slug derived from the title. Create
the directory if needed. It is gitignored, alongside `recordings/`, since
lessons are personal learning material.

Optionally synthesize the story for listening practice — same voice path the app
uses:

```bash
uv run python -c "from kokoro.tts import synthesize; synthesize(open('lessons/<file>.md').read(), 'es', 'lessons/<slug>.wav', play=False)"
```

Pass only the story prose, not the whole markdown file. This pulls in the Kokoro
model, so skip it unless the user asks for audio.

## Follow-up questions

The lesson is built to be interrogated, so expect "what does *le* mean in
sentence 4" or "why *fuera* and not *era*". Answer from the story: quote the
sentence, point at the literal line, explain the form, and give one contrasting
example. If a run of questions reveals a real gap, offer to build the next
lesson around it — that is the loop this repo is trying to close.
