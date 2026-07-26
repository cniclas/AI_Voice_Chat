"""Wikipedia-story lesson curriculum: article selection, story generation,
weakness analysis, homework, and the persistent student profile."""

import json
from datetime import date
from pathlib import Path

import requests

import languages

OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_NUM_CTX = 8192  # default 2048 would overflow once article+story context is added

WIKI_ONTHISDAY_URL = "https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month:02d}/{day:02d}"
WIKI_ACTION_API_URL = "https://en.wikipedia.org/w/api.php"
WIKI_HEADERS = {
    "User-Agent": "AI_Voice_Chat/0.1 (personal language-learning app; niclas.carlstrom88@gmail.com)",
    "Accept": "application/json",
}
WIKI_TIMEOUT = 15

DEFAULT_LEVEL = "B1"  # what a profile that has never been levelled is assumed to be

MAX_CANDIDATES = 10  # candidates offered to the selection LLM
ARTICLE_EXTRACT_CHARS = 3000  # plaintext extract fed to story generation
STORY_WORDS = "between 150 and 200 words"

# Source material is always English Wikipedia regardless of the pair being
# taught: it has by far the richest "on this day" feed, and the extract is only
# ever raw material — the story itself is generated in the target language.

PRE1950_YEAR_CUTOFF = 1950  # bias story topics toward older historical events
MIN_PRE1950_POOL = 3  # below this, fall back to the full day's pool rather than starve selection


# ---------------------------------------------------------------------------
# Ollama helpers
# ---------------------------------------------------------------------------

def chat_completion(messages: list, format: dict | str | None = None, timeout: int = 120,
                    num_ctx: int | None = None) -> str:
    """Low-level Ollama chat call. Returns the assistant's text.

    `format` optionally constrains Ollama's output ("json" or a JSON-schema
    dict) — this only guarantees syntactically valid JSON, callers still
    validate the shape themselves.

    `num_ctx` raises the context window above the default for the few callers
    that need it (the translation review carries several kB of reference
    material plus the transcript); a larger window costs memory, so it is
    opt-in rather than the default.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": num_ctx or OLLAMA_NUM_CTX},
    }
    if format is not None:
        payload["format"] = format
    response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()["message"]["content"]


def chat_completion_json(messages: list, schema: dict, num_ctx: int | None = None) -> dict:
    """chat_completion with a JSON-schema format constraint, parsed and
    retried once on failure. Raises ValueError if both attempts fail."""
    for _ in range(2):
        raw = chat_completion(messages, format=schema, num_ctx=num_ctx)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    raise ValueError("Ollama did not return valid JSON")


# ---------------------------------------------------------------------------
# Wikipedia integration
# ---------------------------------------------------------------------------

def _page_to_candidate(page: dict, source: str) -> dict:
    title = page.get("titles", {}).get("normalized") or page.get("title") or ""
    return {
        "title": title,
        "extract": (page.get("extract") or "").strip(),
        "source": source,
    }


def fetch_onthisday_candidates(today: date | None = None) -> list[dict]:
    """Fetch English Wikipedia's "on this day" events feed for today's
    calendar date (year-independent) and flatten it into candidate dicts:
    {"title", "extract", "source", "year"}.

    Biases toward events older than PRE1950_YEAR_CUTOFF, since older
    historical events tend to make richer, more story-friendly material.
    Falls back to the full deduped pool if too few pre-cutoff candidates
    exist for the day, so sparse days never starve article selection.

    Raises requests.RequestException on network/HTTP failure, ValueError if
    the feed yields no usable candidates.
    """
    d = today or date.today()
    url = WIKI_ONTHISDAY_URL.format(month=d.month, day=d.day)
    response = requests.get(url, headers=WIKI_HEADERS, timeout=WIKI_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    candidates = []
    for event in data.get("events", []):
        pages = event.get("pages") or []
        if not pages:
            continue
        candidate = _page_to_candidate(pages[0], "on this day")
        year = event.get("year")
        event_text = event.get("text", "")
        if event_text:
            year_label = f" ({year})" if year is not None else ""
            candidate["extract"] = f"On this day{year_label}: {event_text} {candidate['extract']}"
        candidate["year"] = year
        candidates.append(candidate)

    seen_titles = set()
    deduped = []
    for c in candidates:
        key = c["title"].casefold()
        if not key or key in seen_titles or len(c["extract"]) < 40:
            continue
        seen_titles.add(key)
        c["extract"] = c["extract"][:300]
        deduped.append(c)

    if not deduped:
        raise ValueError("empty feed")

    pre_cutoff = [c for c in deduped if c.get("year") is not None and c["year"] < PRE1950_YEAR_CUTOFF]
    pool = pre_cutoff if len(pre_cutoff) >= MIN_PRE1950_POOL else deduped

    return pool[:MAX_CANDIDATES]


def fetch_article_extract(title: str, fallback_extract: str = "", max_chars: int = ARTICLE_EXTRACT_CHARS) -> str:
    """Fetch a fuller plaintext extract for `title` via the MediaWiki Action
    API. Returns `fallback_extract` on any parse gap; network errors raise."""
    params = {
        "action": "query",
        "format": "json",
        "prop": "extracts",
        "explaintext": 1,
        "exsectionformat": "plain",
        "exchars": max_chars,
        "redirects": 1,
        "titles": title,
    }
    response = requests.get(WIKI_ACTION_API_URL, params=params, headers=WIKI_HEADERS, timeout=WIKI_TIMEOUT)
    response.raise_for_status()
    try:
        pages = response.json()["query"]["pages"]
        extract = next(iter(pages.values())).get("extract", "")
    except (KeyError, StopIteration, ValueError):
        extract = ""
    return extract or fallback_extract


# ---------------------------------------------------------------------------
# Prompts — article selection
# ---------------------------------------------------------------------------

SELECTION_SYSTEM_PROMPT_TEMPLATE = (
    "You are choosing a topic for a {target_name}-language learning story. "
    "You will be given a numbered list of historical events that happened on "
    "this day, each linked to a Wikipedia topic. "
    "Pick the ONE that would make the most engaging short story for a language "
    "learner: prefer concrete people, places, animals, events, or discoveries "
    "over abstract, technical, or list-like topics. Avoid topics similar to "
    "the recently covered ones listed. "
    "IMPORTANT: this story will be read aloud to a casual learner, so NEVER pick "
    "a topic centered on violence, war, terrorism, massacres, disasters, death, "
    "or other disturbing/traumatic subject matter, even if it is the most "
    "'interesting' option. Among the remaining safe, lighthearted candidates, "
    "pick the most engaging one. Respond with JSON only."
)

SELECTION_USER_TEMPLATE = (
    "Today's candidates:\n{numbered_candidates}\n\n"
    "Recently covered topics (avoid similar ones):\n{recent_titles}\n\n"
    'Return JSON: {{"choice": <number of the best candidate>, '
    '"reason": "<one short sentence>"}}'
)

SELECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["choice", "reason"],
}


def _recent_titles(profile: dict, n: int = 15) -> list[str]:
    return [a["title"] for a in profile.get("articles_covered", [])[-n:]]


def select_article(candidates: list[dict], profile: dict, target: str) -> dict:
    """Pick the most story-friendly candidate. Falls back to candidates[0]
    on any parse/range failure."""
    covered = {t.casefold() for t in _recent_titles(profile, n=1000)}
    filtered = [c for c in candidates if c["title"].casefold() not in covered]
    pool = filtered or candidates

    numbered = "\n".join(
        f"{i}. [{c['source']}] {c['title']} — {c['extract']}" for i, c in enumerate(pool, start=1)
    )
    recent = ", ".join(_recent_titles(profile)) or "(none)"

    messages = [
        {"role": "system", "content": SELECTION_SYSTEM_PROMPT_TEMPLATE.format(
            target_name=languages.name(target))},
        {"role": "user", "content": SELECTION_USER_TEMPLATE.format(
            numbered_candidates=numbered, recent_titles=recent)},
    ]
    try:
        data = chat_completion_json(messages, SELECTION_SCHEMA)
        choice = int(data["choice"])
        if not (1 <= choice <= len(pool)):
            raise ValueError("choice out of range")
        chosen = pool[choice - 1]
        print(f"Today's topic: {chosen['title']} ({chosen['source']}) — {data.get('reason', '')}")
        return chosen
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        print(f"Warning: article selection failed ({e}); using the first candidate.")
        return pool[0]


# ---------------------------------------------------------------------------
# Prompts — story generation
# ---------------------------------------------------------------------------

STORY_GEN_SYSTEM_PROMPT_TEMPLATE = (
    "You write short stories for {level} learners of {target_name}. Write in "
    "clear, natural {target_name}: short sentences, common vocabulary, and only "
    "the verb forms a {level} reader can handle. The story is read ALOUD by a "
    "speech synthesizer, so: no section headings, no lists, no parentheses, no "
    "unusual quotation marks, no digits or special characters — plain prose "
    "paragraphs only, spell numbers out. "
    "Write the story and its title in {target_name}, not in {native_name}. "
    "Respond with JSON only."
)

STORY_GEN_USER_TEMPLATE = (
    "Write a semi-fictional short story ({story_words}) in {target_name}, "
    "inspired by this Wikipedia article. You may invent characters and detail, "
    "but the real subject of the article must stay recognizable.\n\n"
    "Article: {article_title}\n{article_extract}\n\n"
    "{vocab_instruction}"
    'Return JSON: {{"title": "<short title in {target_name}>", '
    '"story": "<the whole story in {target_name}>"}}'
)

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "story": {"type": "string"},
    },
    "required": ["title", "story"],
}


# ---------------------------------------------------------------------------
# Goals — what the student is deliberately working toward
# ---------------------------------------------------------------------------
#
# `weaknesses` is written by the analysis pass: it says what the student got
# wrong, reactively. `goals` is written by the progress-review skill and says
# what they decided to fix, which is the only part of the profile that reflects
# an intention rather than an observation. Everything here degrades to "no
# goals" on a profile that predates the key, which is every existing one.

def active_goals(profile: dict, kind: str | None = None) -> list[dict]:
    """Goals still being worked on, optionally filtered to one kind."""
    goals = (profile.get("goals") or {}).get("active", [])
    return [
        g for g in goals
        if isinstance(g, dict)
        and g.get("status", "active") == "active"
        and (kind is None or g.get("kind") == kind)
    ]


def goal_focus(profile: dict) -> str | None:
    """The grammar focus the student is currently working toward, if any.

    Returns the canonical focus name (as spelled in the language-lesson skill's
    `Focus patterns` headings) so `skill_refs.pick_focus()` can match it.
    """
    for goal in active_goals(profile, "focus"):
        focus = (goal.get("focus") or "").strip()
        if focus:
            return focus
    return None


def goal_vocab(profile: dict) -> list[str]:
    """Words named by active vocabulary goals, in the order they were set."""
    words: list[str] = []
    seen: set[str] = set()
    for goal in active_goals(profile, "vocab"):
        for word in goal.get("words", []):
            if not isinstance(word, str) or not word.strip():
                continue
            key = word.strip().casefold()
            if key not in seen:
                seen.add(key)
                words.append(word.strip())
    return words


def top_vocab_to_practice(profile: dict, n: int = 5) -> list[str]:
    """Words to weave into today's story: goal words first, then the least
    practiced.

    Without the goal pass the ordering is purely reactive — a word the student
    explicitly set out to learn would wait its turn behind whatever the last
    analysis happened to flag, which is the opposite of what setting a goal is
    for. Goal words are included even if the profile has never logged them.
    """
    chosen = goal_vocab(profile)[:n]
    seen = {w.casefold() for w in chosen}
    entries = sorted(
        profile.get("vocab_to_practice", []),
        key=lambda v: (v.get("times_targeted", 0), v.get("last_seen", "")),
    )
    for entry in entries:
        if len(chosen) >= n:
            break
        word = entry.get("word", "")
        if word and word.casefold() not in seen:
            chosen.append(word)
            seen.add(word.casefold())
    return chosen


def generate_story(article_title: str, article_extract: str, profile: dict,
                   target: str, native: str) -> dict:
    """Returns {"title": str, "story": str}. Raises upward on failure."""
    vocab = top_vocab_to_practice(profile)
    target_name = languages.name(target)
    vocab_instruction = (
        f"Work these words the student needs to practice naturally into the "
        f"story: {', '.join(vocab)}.\n\n" if vocab else ""
    )
    messages = [
        {"role": "system", "content": STORY_GEN_SYSTEM_PROMPT_TEMPLATE.format(
            target_name=target_name,
            native_name=languages.name(native),
            level=profile.get("level") or DEFAULT_LEVEL,
        )},
        {"role": "user", "content": STORY_GEN_USER_TEMPLATE.format(
            story_words=STORY_WORDS,
            target_name=target_name,
            article_title=article_title,
            article_extract=article_extract,
            vocab_instruction=vocab_instruction,
        )},
    ]
    return chat_completion_json(messages, STORY_SCHEMA)


# ---------------------------------------------------------------------------
# Prompts — weakness analysis
# ---------------------------------------------------------------------------

ANALYSIS_SYSTEM_PROMPT_TEMPLATE = (
    "You are an expert {target_name} teacher analyzing a transcript of a spoken "
    "conversation between a student and a tutor. Identify the student's "
    "concrete weaknesses. Only analyze the Student's turns, and only turns "
    "marked ({target}); ignore the Tutor's language, and ignore turns the "
    "student spoke in their native {native_name}. Be specific: quote the "
    "student's actual words as evidence. Respond with JSON only."
)

ANALYSIS_USER_TEMPLATE = (
    "Transcript:\n\n{transcript}\n\n"
    "Return JSON matching this shape:\n"
    '{{"summary": "<2-3 sentence summary of the student\'s performance, in {native_name}>",\n'
    ' "weaknesses": [{{"type": "grammar|vocabulary|expression",\n'
    '   "topic": "<short label in English, e.g. \'preterite vs imperfect\'>",\n'
    '   "evidence": "<the student\'s exact words>",\n'
    '   "correction": "<corrected {target_name}>",\n'
    '   "explanation": "<one sentence in {native_name}>"}}],\n'
    ' "vocab_to_practice": ["<{target_name} words/expressions the student lacked or misused>"]}}\n'
    "Limit weaknesses to the 5 most important. If the student made no notable "
    "errors, return empty lists and say so in the summary."
)

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "weaknesses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["grammar", "vocabulary", "expression"]},
                    "topic": {"type": "string"},
                    "evidence": {"type": "string"},
                    "correction": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["type", "topic", "evidence", "correction", "explanation"],
            },
        },
        "vocab_to_practice": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "weaknesses", "vocab_to_practice"],
}


def analyze_weaknesses(transcript_text: str, target: str, native: str) -> dict:
    names = {
        "target": target,
        "target_name": languages.name(target),
        "native_name": languages.name(native),
    }
    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT_TEMPLATE.format(**names)},
        {"role": "user", "content": ANALYSIS_USER_TEMPLATE.format(
            transcript=transcript_text, **names)},
    ]
    return chat_completion_json(messages, ANALYSIS_SCHEMA)


# ---------------------------------------------------------------------------
# Prompts — homework generation
# ---------------------------------------------------------------------------

HOMEWORK_SYSTEM_PROMPT_TEMPLATE = (
    "You are an expert {target_name} teacher writing a short, targeted homework "
    "assignment in Markdown. Write the explanations in {native_name}, the "
    "student's native language; keep all {target_name} examples in "
    "{target_name}. Keep it focused and doable in 20-30 minutes."
)

HOMEWORK_USER_TEMPLATE = (
    "Today's session analysis (JSON):\n{analysis_json}\n\n"
    "Recurring weaknesses from previous sessions:\n{recurring}\n\n"
    "Today's story topic: {topic}\n\n"
    "Write the homework with these sections:\n"
    "1. **Focus points** — the 2-3 weaknesses to work on, each with the "
    "correction and a one-line rule.\n"
    "2. **Exercises** — 4-6 exercises targeting exactly those weaknesses and "
    "the vocab_to_practice words. Where possible, set the exercises in the "
    "world of today's story topic.\n"
    "3. **Vocabulary list** — the practice words with {native_name} glosses and "
    "one example sentence each.\n"
)

HOMEWORK_FALLBACK_USER_TEMPLATE = (
    "Here is the conversation transcript:\n\n{transcript}\n\n"
    "Recurring weaknesses from previous sessions:\n{recurring}\n\n"
    "Today's story topic: {topic}\n\n"
    "Write a {target_name} homework assignment in Markdown based on the "
    "student's turns. Include:\n"
    "1. A short summary of what the conversation was about.\n"
    "2. The main grammar and vocabulary mistakes the student made, each with "
    "the correction and a brief explanation in {native_name}.\n"
    "3. Useful vocabulary or expressions the student could have used.\n"
    "4. 3-5 practice exercises targeting the student's weaknesses.\n\n"
    "Write explanations in {native_name}, but keep all {target_name} examples "
    "in {target_name}."
)


def top_weaknesses(profile: dict, n: int = 3) -> list[dict]:
    weaknesses = sorted(
        profile.get("weaknesses", []),
        key=lambda w: w.get("occurrences", 0),
        reverse=True,
    )
    return weaknesses[:n]


def _format_recurring(recurring: list[dict]) -> str:
    if not recurring:
        return "(first session)"
    return "\n".join(
        f"- {w['topic']} ({w['type']}, seen {w['occurrences']}x)" for w in recurring
    )


def generate_homework(analysis: dict | None, transcript_text: str,
                       story_title: str | None, recurring: list[dict],
                       target: str, native: str) -> str:
    """Returns Markdown homework text. Uses the structured analysis when
    available, falling back to the raw transcript if analysis is None."""
    topic = story_title if story_title else "(no story this session)"
    recurring_text = _format_recurring(recurring)
    names = {
        "target_name": languages.name(target),
        "native_name": languages.name(native),
    }

    if analysis is not None:
        user_prompt = HOMEWORK_USER_TEMPLATE.format(
            analysis_json=json.dumps(analysis, indent=2, ensure_ascii=False),
            recurring=recurring_text,
            topic=topic,
            **names,
        )
    else:
        user_prompt = HOMEWORK_FALLBACK_USER_TEMPLATE.format(
            transcript=transcript_text,
            recurring=recurring_text,
            topic=topic,
            **names,
        )

    messages = [
        {"role": "system", "content": HOMEWORK_SYSTEM_PROMPT_TEMPLATE.format(**names)},
        {"role": "user", "content": user_prompt},
    ]
    return chat_completion(messages)


def save_session_doc(session_dir: Path, filename: str, title: str, body: str) -> Path:
    """Generic writer used for article.md / story.md / homework.md."""
    path = session_dir / filename
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Student profile persistence
# ---------------------------------------------------------------------------

def _empty_profile(target: str | None = None, native: str | None = None) -> dict:
    return {
        "version": 1,
        "level": DEFAULT_LEVEL,
        "target_lang": target,
        "native_lang": native,
        "articles_covered": [],
        "weaknesses": [],
        "vocab_to_practice": [],
        "goals": {"active": [], "archive": []},
    }


def load_profile(profile_path: Path, target: str | None = None,
                 native: str | None = None) -> dict:
    """Missing file → fresh default. Corrupt JSON → rename to .bak, warn,
    return fresh default. Never raises.

    When `target` is given and the stored profile was built for a different
    target language, the old file is set aside as `student_profile_<lang>.json`
    and a fresh profile is returned. Everything in a profile — weaknesses,
    level, vocabulary, goals naming that language's focus patterns — is about
    one language, and `merge_analysis_into_profile()` would otherwise blend two
    of them into a single tally that describes neither.
    """
    if not profile_path.exists():
        return _empty_profile(target, native)
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        backup = profile_path.with_suffix(".json.bak")
        try:
            profile_path.rename(backup)
        except OSError:
            pass
        print(f"Warning: student profile was corrupt ({e}); starting fresh. Backed up to {backup}.")
        return _empty_profile(target, native)

    if target is None:
        return profile

    stored = profile.get("target_lang")
    if stored is None:
        # Profiles written before this key existed are Spanish by construction —
        # that is the only language the app taught. Stamping rather than
        # archiving keeps Niclas's history intact.
        profile["target_lang"] = target
        profile.setdefault("native_lang", native)
        return profile
    if stored == target:
        return profile

    archived = profile_path.with_name(f"{profile_path.stem}_{stored}.json")
    try:
        profile_path.rename(archived)
        print(f"Note: this profile was for {stored}; set aside as {archived.name}, "
              f"starting a fresh {target} profile.")
    except OSError as e:
        print(f"Warning: could not archive the {stored} profile ({e}); starting fresh anyway.")
    return _empty_profile(target, native)


def save_profile(profile: dict, profile_path: Path) -> None:
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")


def bump_vocab_targeted(profile: dict, words: list[str]) -> None:
    """Increment times_targeted for words actually woven into today's story."""
    today = date.today().isoformat()
    vocab = profile.setdefault("vocab_to_practice", [])
    for word in words:
        key = word.casefold()
        existing = next((e for e in vocab if e.get("word", "").casefold() == key), None)
        if existing:
            existing["times_targeted"] = existing.get("times_targeted", 0) + 1
            existing["last_seen"] = today


def record_article_covered(profile: dict, title: str, session_name: str) -> None:
    profile.setdefault("articles_covered", []).append({
        "title": title,
        "date": date.today().isoformat(),
        "session": session_name,
    })
    profile["articles_covered"] = profile["articles_covered"][-60:]


def record_practice(profile: dict, session_name: str, mode: str, *, focus: str | None = None,
                    level: str | None = None, topic: str | None = None,
                    translated: bool = False, direction: str | None = None,
                    spoken_turns: int = 0) -> None:
    """Append what this session actually practiced to the student's track
    record, and keep a running tally per grammar focus.

    `weaknesses` says what the student gets wrong; this says what they have
    worked on, which is the other half of the picture — it is what lets a
    reader (or a future prompt) see that the subjunctive has been drilled four
    times and `por`/`para` never.

    `direction` is which way a translation challenge went (`into_native` for
    reading, `into_target` for producing). Two sessions on the same focus are
    not the same practice if one only ever asked the student to understand it.
    """
    today = date.today().isoformat()
    log = profile.setdefault("practice_log", [])
    log.append({
        "date": today,
        "session": session_name,
        "mode": mode,
        "focus": focus,
        "level": level,
        "topic": topic,
        "translated": translated,
        "direction": direction,
        "spoken_turns": spoken_turns,
    })
    profile["practice_log"] = log[-60:]

    if focus:
        practiced = profile.setdefault("focuses_practiced", [])
        key = focus.casefold()
        existing = next((f for f in practiced if f.get("focus", "").casefold() == key), None)
        if existing:
            existing["times"] = existing.get("times", 0) + 1
            existing["last_practiced"] = today
        else:
            practiced.append({"focus": focus, "times": 1, "last_practiced": today})
        practiced.sort(key=lambda f: f.get("times", 0), reverse=True)


def merge_analysis_into_profile(profile: dict, analysis: dict | None) -> None:
    """Deterministic merge, no LLM call. No-op if analysis is None (article
    coverage was already recorded eagerly at story-generation time)."""
    if analysis is None:
        return

    today = date.today().isoformat()

    weaknesses = profile.setdefault("weaknesses", [])
    for w in analysis.get("weaknesses", []):
        key = (w.get("type", ""), w.get("topic", "").casefold())
        existing = next(
            (e for e in weaknesses if (e.get("type", ""), e.get("topic", "").casefold()) == key),
            None,
        )
        if existing:
            existing["occurrences"] = existing.get("occurrences", 1) + 1
            existing["last_seen"] = today
            existing["explanation"] = w.get("explanation", existing.get("explanation", ""))
        else:
            weaknesses.append({
                "type": w.get("type", ""),
                "topic": w.get("topic", ""),
                "explanation": w.get("explanation", ""),
                "occurrences": 1,
                "first_seen": today,
                "last_seen": today,
            })
    weaknesses.sort(key=lambda e: e.get("occurrences", 0), reverse=True)
    profile["weaknesses"] = weaknesses[:30]

    vocab = profile.setdefault("vocab_to_practice", [])
    for word in analysis.get("vocab_to_practice", []):
        key = word.casefold()
        existing = next((e for e in vocab if e.get("word", "").casefold() == key), None)
        if existing:
            existing["last_seen"] = today
        else:
            vocab.append({"word": word, "times_targeted": 0, "last_seen": today})
    profile["vocab_to_practice"] = vocab[:40]
