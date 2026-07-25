---
name: language-lesson
description: Build a graded language lesson from a piece of source material — a short story, an article, a paragraph of facts, a Wikipedia extract — targeting a specific grammar focus (past tenses, conditional, subjunctive, aspect, cases, or any combination) at a chosen CEFR level (A1-C2), paired with a word-for-word literal translation so every single word and phrase can be traced back. Defaults to Spanish for an English speaker; works for any language pair. Use this skill whenever the user wants a story, reading, text, or lesson written for language practice, wants an existing text rewritten or re-graded to a level, wants to drill a tense or grammar point through reading, or asks for an interlinear/literal/word-for-word translation alongside a natural one. Trigger it even when the user just pastes some text and says something like "make me a B1 lesson about this focusing on the past tenses" without using the word "skill" or "lesson".
---

# Language lesson builder

Produce a lesson with two halves that must stay in lockstep: a story written to
a level and a grammar focus in the target language, and an aligned translation
that lets the learner interrogate any word in it.

The reason the translation matters so much: this repo is a speaking tutor, and a
learner reading alone hits a word, wonders "why is it *le* and not *lo*", and has
nobody to ask. A normal translation can't answer that — it dissolves the target
language into fluent English and the mapping is gone. So the lesson carries a
*literal* line that preserves the source word order and morphology, plus a
natural line so the meaning still lands. The learner can then point at any word
and the answer is already on the page.

## Inputs

Five things shape a lesson. Only the first is essential — fill in the rest from
context, state what you assumed in one line, and get on with writing:

| Input | How to resolve it if not given |
|---|---|
| **Source context** — the story, facts, or article to build from | Required. If genuinely absent, ask for it; everything else is guessable, this isn't. |
| **Focus** — the grammar being drilled | Read the source: a historical account wants past tenses, a plan or forecast wants future and conditional. Propose one and say so. Or pull the top recurring weakness from the student profile. |
| **Level** — A1-C2 | `recordings/student_profile.json` → `"level"`. Falls back to B1, which is what this repo assumes. |
| **Theme / tone** — cozy, mysterious, comic, documentary | Optional. Default to warm and concrete; the app reads these aloud to a casual learner, so avoid violence, disaster, and death even when the source is full of them. |
| **Language pair** | Target Spanish, native English, unless told otherwise. |

## Reading the references

Two files, split along the seam between what generalizes and what doesn't:

- `references/levels.md` — the CEFR bands. Text budget, clause complexity, and
  the *functions* a reader can handle at each level. Language-neutral, so read
  the band for the requested level whatever the target language is.
- `references/languages/<code>.md` — how that language delivers those functions:
  which forms belong at which level, the focus patterns for that language's
  characteristic difficulties, and its glossing quirks. Read the level row and
  the focus section.

The split exists because "can narrate past events with texture" is a fact about
B1 readers everywhere, while the forms that deliver it are the Spanish
imperfect, the German Präteritum, or Japanese だった — and each language draws
its own line about which arrives when. Keeping them apart means a new language
costs one file, not a fork of everything.

Skipping these is the usual failure mode. Written from feel, "B1 Spanish" drifts
a level or two upward within a paragraph and the learner bounces off a text they
were supposed to be able to read.

If `recordings/student_profile.json` exists, also pull `vocab_to_practice` and
the top `weaknesses`; weaving a few of those words into the story costs nothing
and makes the lesson land on what this particular student keeps missing.

### If the target language has no file yet

Only `es.md` ships complete. For any other language, work from `levels.md` plus
what you know of the language — that knowledge is not the bottleneck, consistency
between lessons is. So write what you worked out to
`references/languages/<code>.md` as you go, covering:

1. **Form inventory by level**, cumulative, with an explicit "out" list per
   level — drift happens by quietly importing a form from the row below.
2. **A sample sentence per level**, which calibrates faster than any description.
3. **Focus patterns** for that language's real difficulties, each with what to
   force into the story, the mistake an English speaker reliably makes, and how
   to gloss it. Pick the points that actually bite: cases and separable verbs for
   German, aspect pairs for Russian, particles and politeness levels for
   Japanese, tones and classifiers for Mandarin.
4. **Glossing notes** — what the language encodes that English doesn't, and how
   the literal line should render it.

Match `es.md`'s shape. The next lesson in that language then costs a file read
instead of a re-derivation, and two lessons a month apart stay calibrated to the
same scale.

## Writing the story

Build a *new* semi-fictional story from the source rather than summarizing it.
Invent people and detail, keep the real subject matter recognizable. The story
exists to carry the grammar focus, so plot it around situations that demand the
target structures — a lesson on past tenses needs a narrator remembering
something, a lesson on conditionals needs a choice or a hypothetical.

Two constraints come from the app itself:

- **It may be read aloud** by Kokoro TTS (`kokoro/tts.py`). Plain prose
  paragraphs only — no headings inside the story, no lists, no parentheses, no
  quotation marks beyond ordinary dialogue, no digits or symbols a synthesizer
  will mangle. Spell numbers out.
- **It gets discussed afterwards**, so it needs something to talk about: a
  person with a motive, a small turn of events, a question left open.

**Contrast beats repetition.** A story using the preterite twelve times teaches
nothing about the preterite; one that puts a preterite and an imperfect in the
same sentence doing different jobs makes the distinction visible in a way no rule
statement does. Plot the story so the contrast is forced by events rather than
sprinkled on. The language file says what "contrast" means for each focus.

Two focuses is the limit, and they should be ones that interact naturally —
past tenses with object pronouns works because narration needs both; subjunctive
with por/para doesn't, and the story will visibly strain. Give the primary focus
about two thirds of the weight. If there's a sentence where the two genuinely
interact, point at it in the notes; it's the most valuable one in the lesson.

When the focus is vocabulary or a topic field rather than grammar, put each
target word in a context that makes it inferable without the gloss, then reuse it
once in a *different* context — a word met twice in different frames is retained
far better than one met twice in the same frame. Keep the grammar at level and
unremarkable so attention stays on the lexis.

## The aligned translation

This is the part worth slowing down for. Number every sentence of the story and
give each one two lines beneath it.

**The literal line** maps the target language to English word by word, in the
original order. It is not English and should not read like English. If it reads
smoothly you have almost certainly smoothed away the thing the learner needed to
see.

Notation, kept deliberately small so it stays readable:

- `-` joins English words that translate a single word in the original:
  `contaba` → `used-to-tell`; `del` → `of-the`.
- `[ ]` supplies meaning the language encodes without a separate word — dropped
  subjects, the implied "there" of *hay*: `Fue al mercado.` →
  `[He] went to-the market.`
- `/` offers alternatives where a form is genuinely ambiguous out of context:
  `le` → `to-him/her`.
- Grammar tags like `(subj)`, `(impf)`, `(pret)`, `(acc)`, `(perfective)` go in
  **only when the focus turns on that distinction**. On a past-tense lesson,
  marking imperfect against preterite is the whole point; on a vocabulary lesson
  it is noise.
- Punctuation mirrors the original, including marks English lacks like `¿` and
  `¡`.

**The natural line** is ordinary, idiomatic English — what a translator would
actually write. It exists so the learner can check comprehension after decoding.

Worked examples, Spanish:

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

The same discipline transfers: a German literal line will scramble under V2 word
order and want case tags, a Japanese one will gloss particles as standalone
chunks. The principle is constant — preserve what the original does, however
strange it looks.

## Focus notes and vocabulary

After the aligned reading, explain the grammar in English using sentences pulled
from *this* story rather than invented textbook examples — the learner has just
met them in context, which is the moment the rule sticks. Three or four points,
each with the example, the rule in one line, and where an English speaker
typically goes wrong.

Then a vocabulary table for words at or above the level, and any idiom whose
literal line looked strange. Skip the obvious cognates.

Close with a few speaking prompts in the target language that can't be answered
without using the target structure. They feed straight into the app's
conversation phase, which is where the reading turns into production.

## Output template

Section headings go in the target language, as below for Spanish.

```markdown
# <Title in the target language>

**Nivel:** B1 · **Enfoque:** pretérito vs imperfecto · **Tema:** <theme>
**Source:** <what the lesson was built from, one line>

## Historia

<plain prose, unnumbered, TTS-safe>

## Lectura alineada

**1.** <sentence in the target language>
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

1. <question that forces the target structure>
```

The story appears twice — once as unbroken prose, once split across the aligned
reading. That is intentional: the prose version is what gets read aloud and read
for pleasure, and breaking it into numbered fragments would ruin both.

## Saving the lesson

Write to `lessons/<YYYY-MM-DD>-<lang>-<slug>.md`, slug derived from the title.
Create the directory if needed. It is gitignored, alongside `recordings/`, since
lessons are personal learning material.

Optionally synthesize the story for listening practice — same voice path the app
uses:

```bash
uv run python -c "from kokoro.tts import synthesize; synthesize('<story prose>', 'es', 'lessons/<slug>.wav', play=False)"
```

Pass only the story prose, not the whole markdown file. This pulls in the Kokoro
model, so skip it unless the user asks for audio. Note that `kokoro/tts.py` only
has voices for `en` and `es` — lessons in other languages are text-only unless a
voice is added there first.

## Follow-up questions

The lesson is built to be interrogated, so expect "what does *le* mean in
sentence 4" or "why *fuera* and not *era*". Answer from the story: quote the
sentence, point at the literal line, explain the form, and give one contrasting
example. If a run of questions reveals a real gap, offer to build the next
lesson around it — that is the loop this repo is trying to close.
