# CEFR calibration for Spanish story writing

Read the row for the requested level. The point of this file is to stop the
drift that happens when a level is written from feel: asked for "B1", a model
reaches for the subjunctive and 25-word sentences, and the learner bounces off
a text they were supposed to be able to read.

A useful test before you finish: could a learner at this level read the story
once, out loud, and follow it without stopping? If they'd need to reread a
sentence to parse it, that sentence belongs a level up.

## A1

- **Length:** 60-100 words. Sentences under 8 words.
- **Verbs:** present indicative only. `ser`, `estar`, `hay`, `tener`, `gustar`.
  Immediate future via `ir a` + infinitive. Regular verbs plus the handful of
  irregulars that are unavoidable.
- **Vocabulary:** concrete and everyday — family, house, food, weather, numbers,
  colors, routine. Cognates are welcome; they build confidence.
- **Structures:** simple main clauses joined by `y`, `pero`, `porque`. Adjective
  after noun. No object pronouns beyond a very occasional `me`/`te`.
- **Avoid:** any past tense, subjunctive, relative clauses, passive.
- **Sounds like:** *María vive en un pueblo pequeño. Tiene un perro negro. Todos
  los días camina al mercado con su madre.*

## A2

- **Length:** 100-150 words. Sentences under 12 words.
- **Verbs:** adds pretérito indefinido and imperfecto, though kept in separate
  sentences rather than contrasted within one. Pretérito perfecto (`he hablado`).
  Reflexives. Simple future.
- **Vocabulary:** everyday plus travel, work, health, past experiences.
- **Structures:** direct and indirect object pronouns, comparatives, `porque` /
  `cuando` / `mientras` clauses, `hay que` and `tener que`.
- **Avoid:** subjunctive, conditional, pluperfect, `se` impersonal/passive.
- **Sounds like:** *Ayer Ana perdió el autobús. Cuando llegó a la oficina, su
  jefe ya estaba enfadado. Le explicó todo y él la escuchó con paciencia.*

## B1

The repo's default, and what `curriculum.py` generates.

- **Length:** 150-220 words. Sentences under 18 words.
- **Verbs:** pretérito and imperfecto in genuine contrast — this is the level
  where narration becomes possible. Future simple, conditional simple,
  pluscuamperfecto. Present subjunctive after the common triggers: `quiero que`,
  `espero que`, `es importante que`, `cuando` + future reference.
- **Vocabulary:** abstract enough for feelings, opinions, and plans. Some
  common idiom, glossed.
- **Structures:** relative clauses with `que` and `donde`, `por` vs `para`,
  `se` impersonal, indirect speech in the present.
- **Avoid:** imperfect subjunctive, si-clauses beyond the real type, dense
  literary register.
- **Sounds like:** *Cuando era joven, Tomás creía que el río nunca cambiaba.
  Aquella mañana de invierno, sin embargo, descubrió que la corriente se había
  llevado el puente que su abuelo construyó.*

## B2

- **Length:** 220-300 words. Sentences can run to 25 words if the structure
  earns it.
- **Verbs:** imperfect subjunctive, si-clauses of the hypothetical type
  (`si tuviera... haría`), full compound tenses, passive with `ser` and the
  `se` passive.
- **Vocabulary:** nuance and connotation — near-synonyms that differ in
  register, phrasal idiom, some figurative language.
- **Structures:** discourse connectors (`sin embargo`, `a pesar de que`,
  `en cuanto`), subordination two clauses deep, reported speech with backshift.
- **Sounds like:** *Aunque le hubiera gustado quedarse, sabía que si no tomaba
  el tren de las seis perdería la única oportunidad que le quedaba.*

## C1

- **Length:** 300-400 words.
- **Verbs:** the full subjunctive range, including pluscuamperfecto de
  subjuntivo and the counterfactual si-clause (`si hubiera sabido, habría...`).
  Aspect used for effect rather than rule-following.
- **Vocabulary:** precise, idiomatic, occasionally literary. Regionalisms are
  fine if flagged in the vocabulary table.
- **Structures:** long periodic sentences, inversion for emphasis, irony and
  implicature, `lo` + adjective nominalizations.

## C2

- **Length:** 350-450 words.
- Near-native. Wordplay, ambiguity, shifting register within a paragraph,
  cultural and literary allusion. The interest lies in *how* it is said.
- The literal gloss becomes most valuable here, since the gap between the
  literal and natural lines is where the style actually lives.

## What changes across levels in the translation

The Spanish moves; the natural English line does not. It stays ordinary and
idiomatic at every level — a C1 story does not get a fancier English rendering,
it gets a more distant one from the literal line.

The literal line, on the other hand, gets busier as the level rises: more
bracketed subjects, more grammar tags, more hyphenated compounds where one
Spanish word needs three English ones. That widening gap is itself the lesson.
