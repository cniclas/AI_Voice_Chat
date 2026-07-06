"""Wikipedia-story lesson curriculum: article selection, story generation,
weakness analysis, homework, and the persistent student profile."""

import json
import os
from datetime import date
from pathlib import Path

import requests

_ROOT = os.path.dirname(os.path.abspath(__file__))

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

PROFILE_PATH = Path(_ROOT) / "recordings" / "student_profile.json"

MAX_CANDIDATES = 10  # candidates offered to the selection LLM
ARTICLE_EXTRACT_CHARS = 3000  # plaintext extract fed to story generation
STORY_WORDS = "entre 150 y 200 palabras"

PRE1950_YEAR_CUTOFF = 1950  # bias story topics toward older historical events
MIN_PRE1950_POOL = 3  # below this, fall back to the full day's pool rather than starve selection


# ---------------------------------------------------------------------------
# Ollama helpers
# ---------------------------------------------------------------------------

def chat_completion(messages: list, format: dict | str | None = None, timeout: int = 120) -> str:
    """Low-level Ollama chat call. Returns the assistant's text.

    `format` optionally constrains Ollama's output ("json" or a JSON-schema
    dict) — this only guarantees syntactically valid JSON, callers still
    validate the shape themselves.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": OLLAMA_NUM_CTX},
    }
    if format is not None:
        payload["format"] = format
    response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()["message"]["content"]


def chat_completion_json(messages: list, schema: dict) -> dict:
    """chat_completion with a JSON-schema format constraint, parsed and
    retried once on failure. Raises ValueError if both attempts fail."""
    for _ in range(2):
        raw = chat_completion(messages, format=schema)
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

SELECTION_SYSTEM_PROMPT = (
    "You are choosing a topic for a Spanish-language learning story. "
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


def select_article(candidates: list[dict], profile: dict) -> dict:
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
        {"role": "system", "content": SELECTION_SYSTEM_PROMPT},
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

STORY_GEN_SYSTEM_PROMPT = (
    "Eres un escritor de cuentos cortos para estudiantes de español de nivel "
    "intermedio (B1). Escribes en español claro y natural: frases cortas, "
    "vocabulario común, tiempos verbales sencillos (presente, pretérito, "
    "imperfecto). El cuento se leerá EN VOZ ALTA por un sintetizador de voz, "
    "así que: sin títulos de sección, sin listas, sin paréntesis, sin comillas "
    "raras, sin caracteres especiales; solo párrafos de prosa. "
    "Responde únicamente con JSON."
)

STORY_GEN_USER_TEMPLATE = (
    "Escribe un cuento corto semi-ficticio ({story_words}) inspirado en este "
    "artículo de Wikipedia. Puede inventar personajes y detalles, pero debe "
    "reflejar el tema real del artículo.\n\n"
    "Artículo: {article_title}\n{article_extract}\n\n"
    "{vocab_instruction}"
    'Devuelve JSON: {{"title": "<título corto en español>", '
    '"story": "<el cuento completo en español>"}}'
)

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "story": {"type": "string"},
    },
    "required": ["title", "story"],
}


def top_vocab_to_practice(profile: dict, n: int = 5) -> list[str]:
    words = sorted(
        profile.get("vocab_to_practice", []),
        key=lambda v: (v.get("times_targeted", 0), v.get("last_seen", "")),
    )
    return [w["word"] for w in words[:n]]


def generate_story(article_title: str, article_extract: str, profile: dict) -> dict:
    """Returns {"title": str, "story": str}. Raises upward on failure."""
    vocab = top_vocab_to_practice(profile)
    vocab_instruction = (
        f"Incorpora de forma natural estas palabras que el estudiante necesita "
        f"practicar: {', '.join(vocab)}.\n\n" if vocab else ""
    )
    messages = [
        {"role": "system", "content": STORY_GEN_SYSTEM_PROMPT},
        {"role": "user", "content": STORY_GEN_USER_TEMPLATE.format(
            story_words=STORY_WORDS,
            article_title=article_title,
            article_extract=article_extract,
            vocab_instruction=vocab_instruction,
        )},
    ]
    return chat_completion_json(messages, STORY_SCHEMA)


# ---------------------------------------------------------------------------
# Prompts — weakness analysis
# ---------------------------------------------------------------------------

ANALYSIS_SYSTEM_PROMPT = (
    "You are an expert Spanish teacher analyzing a transcript of a spoken "
    "conversation between a student and a tutor. Identify the student's "
    "concrete weaknesses. Only analyze the Student's turns, and only turns "
    "marked (es); ignore the Tutor's language. Be specific: quote the "
    "student's actual words as evidence. Respond with JSON only."
)

ANALYSIS_USER_TEMPLATE = (
    "Transcript:\n\n{transcript}\n\n"
    "Return JSON matching this shape:\n"
    '{{"summary": "<2-3 sentence English summary of the student\'s performance>",\n'
    ' "weaknesses": [{{"type": "grammar|vocabulary|expression",\n'
    '   "topic": "<short label, e.g. \'preterite vs imperfect\'>",\n'
    '   "evidence": "<the student\'s exact words>",\n'
    '   "correction": "<corrected Spanish>",\n'
    '   "explanation": "<one sentence in English>"}}],\n'
    ' "vocab_to_practice": ["<Spanish words/expressions the student lacked or misused>"]}}\n'
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


def analyze_weaknesses(transcript_text: str) -> dict:
    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
        {"role": "user", "content": ANALYSIS_USER_TEMPLATE.format(transcript=transcript_text)},
    ]
    return chat_completion_json(messages, ANALYSIS_SCHEMA)


# ---------------------------------------------------------------------------
# Prompts — homework generation
# ---------------------------------------------------------------------------

HOMEWORK_SYSTEM_PROMPT = (
    "You are an expert Spanish teacher writing a short, targeted homework "
    "assignment in Markdown. Explanations in English; all Spanish examples in "
    "Spanish. Keep it focused and doable in 20-30 minutes."
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
    "3. **Vocabulary list** — the practice words with English glosses and one "
    "example sentence each.\n"
)

HOMEWORK_FALLBACK_USER_TEMPLATE = (
    "Here is the conversation transcript:\n\n{transcript}\n\n"
    "Recurring weaknesses from previous sessions:\n{recurring}\n\n"
    "Today's story topic: {topic}\n\n"
    "Write a Spanish homework assignment in Markdown based on the student's "
    "turns. Include:\n"
    "1. A short summary of what the conversation was about.\n"
    "2. The main grammar and vocabulary mistakes the student made, each with "
    "the correction and a brief explanation in English.\n"
    "3. Useful vocabulary or expressions the student could have used.\n"
    "4. 3-5 practice exercises targeting the student's weaknesses.\n\n"
    "Write explanations in English, but keep all Spanish examples in Spanish."
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
                       story_title: str | None, recurring: list[dict]) -> str:
    """Returns Markdown homework text. Uses the structured analysis when
    available, falling back to the raw transcript if analysis is None."""
    topic = story_title if story_title else "(no story this session)"
    recurring_text = _format_recurring(recurring)

    if analysis is not None:
        user_prompt = HOMEWORK_USER_TEMPLATE.format(
            analysis_json=json.dumps(analysis, indent=2, ensure_ascii=False),
            recurring=recurring_text,
            topic=topic,
        )
    else:
        user_prompt = HOMEWORK_FALLBACK_USER_TEMPLATE.format(
            transcript=transcript_text,
            recurring=recurring_text,
            topic=topic,
        )

    messages = [
        {"role": "system", "content": HOMEWORK_SYSTEM_PROMPT},
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

def _empty_profile() -> dict:
    return {
        "version": 1,
        "level": "B1",
        "articles_covered": [],
        "weaknesses": [],
        "vocab_to_practice": [],
    }


def load_profile() -> dict:
    """Missing file → fresh default. Corrupt JSON → rename to .bak, warn,
    return fresh default. Never raises."""
    if not PROFILE_PATH.exists():
        return _empty_profile()
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        backup = PROFILE_PATH.with_suffix(".json.bak")
        try:
            PROFILE_PATH.rename(backup)
        except OSError:
            pass
        print(f"Warning: student profile was corrupt ({e}); starting fresh. Backed up to {backup}.")
        return _empty_profile()


def save_profile(profile: dict) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")


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
