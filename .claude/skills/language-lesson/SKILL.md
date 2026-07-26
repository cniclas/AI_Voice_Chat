---
name: language-lesson
description: Build a graded language lesson from a piece of source material — a short story, an article, a paragraph of facts, a Wikipedia extract — targeting a specific grammar focus (past tenses, conditional, subjunctive, aspect, cases, or any combination) at a chosen CEFR level (A1-C2), paired with a word-for-word literal translation so every single word and phrase can be traced back. Works for any language pair; the default pair is the active student's, as configured in `users.py`. Use this skill whenever the user wants a story, reading, text, or lesson written for language practice, wants an existing text rewritten or re-graded to a level, wants to drill a tense or grammar point through reading, or asks for an interlinear/literal/word-for-word translation alongside a natural one. Trigger it even when the user just pastes some text and says something like "make me a B1 lesson about this focusing on the past tenses" without using the word "skill" or "lesson".
---

# Language lesson builder

Produce a lesson with two halves that must stay in lockstep: a story written to
a level and a grammar focus in the target language, and an aligned translation
that lets the learner interrogate any word in it.

The reason the translation matters so much: this repo is a speaking tutor, and a
learner reading alone hits a word, wonders why that form and not the one they
expected, and has nobody to ask. A normal translation can't answer that — it
dissolves the target language into fluent prose and the mapping is gone. So the
lesson carries a *literal* line that preserves the source word order and
morphology, plus a natural line so the meaning still lands. The learner can then point at any word
and the answer is already on the page.

## Inputs

Before any of these: confirm which student the lesson is for — Niclas or
Alejandra — if that isn't already clear from the conversation. It decides the
`<user>` segment in every path below, so get it settled first.

Five things shape a lesson. Only the first is essential — fill in the rest from
context, state what you assumed in one line, and get on with writing:

| Input | How to resolve it if not given |
|---|---|
| **Source context** — the story, facts, or article to build from | Required. If genuinely absent, ask for it; everything else is guessable, this isn't. |
| **Focus** — the grammar being drilled | Read the source: a historical account wants past tenses, a plan or forecast wants future and conditional. Propose one and say so. Or pull the top recurring weakness from the student profile. |
| **Level** — A1-C2 | `recordings/<user>/student_profile.json` → `"level"`. Falls back to B1, which is what this repo assumes. |
| **Theme / tone** — cozy, mysterious, comic, documentary | Optional. Default to warm and concrete; the app reads these aloud to a casual learner, so avoid violence, disaster, and death even when the source is full of them. |
| **Language pair** | The student's own, from their `users.py` entry — `native_lang` and `target_lang` (Niclas: English → Spanish; Alejandra: Spanish → French). Only override if the user asks for a different pair outright. |

## Reading the references

Two files, split along the seam between what generalizes and what doesn't:

- `references/levels.md` — the CEFR bands. Text budget, clause complexity, and
  the *functions* a reader can handle at each level. Language-neutral, so read
  the band for the requested level whatever the target language is.
- `references/languages/<code>.md` — how that language delivers those functions:
  which forms belong at which level, the focus patterns for that language's
  characteristic difficulties, and its glossing quirks. Read the level row and
  the focus section.

There is a third file, and it is the one people forget:
`translation-review/references/languages/<target>-<native>.md`, the trap
catalogue for this specific direction. It says what *this* native language
misreads in *this* target language, which is what the focus notes need in order
to say something more useful than "learners find this hard".

The split exists because "can narrate past events with texture" is a fact about
B1 readers everywhere, while the forms that deliver it are one language's
imperfect, another's compound past, another's aspect particle — and each
language draws its own line about which arrives when. Keeping them apart means a
new language costs one file, not a fork of everything.

Skipping these is the usual failure mode. Written from feel, "B1" in any
language drifts a level or two upward within a paragraph and the learner bounces
off a text they were supposed to be able to read.

If `recordings/<user>/student_profile.json` exists, also pull `vocab_to_practice` and
the top `weaknesses`; weaving a few of those words into the story costs nothing
and makes the lesson land on what this particular student keeps missing.

### If the target language has no file yet

`es.md` and `fr.md` ship complete. For any other language, work from `levels.md`
plus what you know of the language — that knowledge is not the bottleneck, consistency
between lessons is. So write what you worked out to
`references/languages/<code>.md` as you go, covering:

1. **Form inventory by level**, cumulative, with an explicit "out" list per
   level — drift happens by quietly importing a form from the row below.
2. **A sample sentence per level**, which calibrates faster than any description.
3. **Focus patterns** for that language's real difficulties, each with what to
   force into the story, the mistake a learner reliably makes, and how to gloss
   it. Where an error belongs to one native language rather than to learners
   generally, label it as such. Pick the points that actually bite: cases and
   separable verbs for German, aspect pairs for Russian, particles and politeness
   levels for Japanese, tones and classifiers for Mandarin.
4. **Glossing notes** — what the language encodes that the native one doesn't,
   and how the literal line should render it. Put the shared observations in the
   section body and the worked examples under `### Into <native language>`, since
   a gloss only exists in one language at a time and `skill_refs.glossing_notes()`
   reads the two together.

Match `es.md`'s and `fr.md`'s shape — the `## Form inventory by level`,
`## Focus patterns` and `## Glossing notes` headings and the `###` names beneath
them are read directly by `skill_refs.py`, so a renamed section silently
disappears from what the app generates. The next lesson in that language then costs a file read
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

**The literal line** maps the target language into the student's native language
word by word, in the original order. It is not natural prose and should not read
like it. If it reads smoothly you have almost certainly smoothed away the thing
the learner needed to see.

Notation, kept deliberately small so it stays readable:

- `-` joins native-language words that translate a single word in the original:
  a one-word imperfect becoming `used-to-tell`; a contraction becoming
  `of-the`.
- `[ ]` supplies meaning the language encodes without a separate word — a
  dropped subject, an implied existential: `[He] went to-the market.`
- `/` offers alternatives where a form is genuinely ambiguous out of context:
  a pronoun unmarked for gender becoming `to-him/her`.
- Grammar tags like `(subj)`, `(impf)`, `(pret)`, `(acc)`, `(perfective)` go in
  **only when the focus turns on that distinction**. On a past-tense lesson,
  marking imperfect against preterite is the whole point; on a vocabulary lesson
  it is noise.
- Punctuation mirrors the original, including marks the native language lacks
  (Spanish `¿` and `¡`, French `«  »`).

**The natural line** is ordinary, idiomatic prose *in the student's native
language* — what a translator would actually write. It exists so the learner can
check comprehension after decoding. Both lines are in the native language; only
the story is in the target one.

The worked examples live in the language file, under `## Glossing notes` →
`### Into <native language>`, because a gloss only exists in one language at a
time and a Spanish→English example teaches a Spanish speaker reading French
nothing. Read them there before writing any. The shape they all follow:

> **1.** <sentence in the target language>
> - *Lit.* <word-for-word, source order, brackets and tags as above>
> - *Nat.* <what a translator would write>

Idioms are where the literal line earns its place. If the target language builds
a common expression out of a different verb than the native one does, the literal
line must show the strange version and let the natural line fix it — a learner
who never sees the construction keeps rebuilding it out of their own language.
Flag it in the vocabulary table if it is likely to trip someone up.

The same discipline transfers to any pair: a German literal line will scramble
under V2 word order and want case tags, a Japanese one will gloss particles as
standalone chunks. The principle is constant — preserve what the original does,
however strange it looks.

## Focus notes and vocabulary

After the aligned reading, explain the grammar in the student's native language
using sentences pulled from *this* story rather than invented textbook examples —
the learner has just met them in context, which is the moment the rule sticks.
Three or four points, each with the example, the rule in one line, and where a
speaker of this particular native language typically goes wrong. The pair's trap
catalogue is where that last part comes from.

Then a vocabulary table for words at or above the level, and any idiom whose
literal line looked strange. Skip the obvious cognates — but not the false
friends, which for a close pair are the most valuable rows in the table.

Close with a few speaking prompts in the target language that can't be answered
without using the target structure. They feed straight into the app's
conversation phase, which is where the reading turns into production.

## Output template

Section headings go in the **target** language — the lesson is an artifact of
that language, not a document about it. `languages.py` holds the exact wording
the app uses for each one (`LessonHeadings`), so a hand-written lesson and a
generated one look the same; match it. For Spanish that is Historia · Lectura
alineada · Notas de gramática · Vocabulario · Para hablar, and for French
Histoire · Lecture alignée · Notes de grammaire · Vocabulaire · Pour parler.

```markdown
# <Title in the target language>

**<Level>:** B1 · **<Focus>:** <focus name from the language file> · **<Theme>:** <theme>
**Source:** <what the lesson was built from, one line>

## <Story>

<plain prose, unnumbered, TTS-safe>

## <Aligned reading>

**1.** <sentence in the target language>
- *Lit.* <word-for-word gloss, in the native language>
- *Nat.* <idiomatic native language>

**2.** ...

## <Grammar notes> — <focus>

**<point>** — *<example from the story>*
<one-line rule, then the mistake a speaker of this native language typically makes>

## <Vocabulary>

| <target language> | <Literal> | <native language> | <Note> |
|---|---|---|---|

## <Speaking prompts>

1. <question that forces the target structure>
```

The story appears twice — once as unbroken prose, once split across the aligned
reading. That is intentional: the prose version is what gets read aloud and read
for pleasure, and breaking it into numbered fragments would ruin both.

## Saving the lesson

Write to `lessons/<user>/<YYYY-MM-DD>-<lang>-<slug>.md`, slug derived from the
title. Create the directory if needed. It is gitignored, alongside
`recordings/`, since lessons are personal learning material.

Optionally synthesize the story for listening practice — same voice path the app
uses:

```bash
uv run python -c "from kokoro.tts import synthesize; synthesize('<story prose>', '<target lang code>', 'lessons/<user>/<slug>.wav', play=False)"
```

Pass only the story prose, not the whole markdown file. This pulls in the Kokoro
model, so skip it unless the user asks for audio. Voices come from
`languages.py`: any entry whose `tts` is set can be spoken (`en`, `es`, `fr`
today), and lessons in a language without one are text-only unless a voice is
added there first.

## Follow-up questions

The lesson is built to be interrogated, so expect "what does this word mean in
sentence 4" or "why this form and not that one". Answer from the story: quote the
sentence, point at the literal line, explain the form, and give one contrasting
example. If a run of questions reveals a real gap, offer to build the next
lesson around it — that is the loop this repo is trying to close.
