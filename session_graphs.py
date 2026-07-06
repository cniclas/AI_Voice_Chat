"""LangGraph workflows for the deterministic phases of a learning session.

Two graphs, per the architecture recommendation:

- ``session_setup_graph``   — load profile → fetch feed → enrich candidates →
  select article → fetch details → generate story → save session files.
- ``session_analysis_graph`` — analyze transcript → generate homework →
  update student profile → write homework.

The real-time voice conversation loop stays a plain loop in ``main.py``;
LangGraph adds nothing for interactive audio and would get in the way.

Any recoverable failure in the setup graph sets ``setup_failed`` and routes
straight to END so ``main.py`` can fall back to a plain conversation.
"""

import json
from pathlib import Path
from typing import Optional, TypedDict

import requests
from langgraph.graph import StateGraph, START, END

import curriculum

# Candidate extracts are enriched to this length before article selection —
# richer summaries improve the LLM's pick at the cost of one extra HTTP call
# per candidate.
CANDIDATE_SUMMARY_CHARS = 800


class SessionState(TypedDict, total=False):
    session_dir: str            # str (not Path) so the state stays serializable
    profile: dict
    candidates: list
    article: dict
    article_extract: str
    story: dict                 # {"title": ..., "story": ...}
    practice_words: list
    transcript_text: str
    analysis: Optional[dict]
    recurring: list
    homework: Optional[str]
    setup_failed: str           # reason the story arc was skipped


# ---------------------------------------------------------------------------
# Session setup graph
# ---------------------------------------------------------------------------

def load_profile_node(state: SessionState) -> dict:
    return {"profile": curriculum.load_profile()}


def fetch_featured_feed_node(state: SessionState) -> dict:
    try:
        return {"candidates": curriculum.fetch_featured_candidates()}
    except (requests.RequestException, ValueError) as e:
        return {"setup_failed": f"Wikipedia feed unavailable: {e}"}


def extract_candidates_node(state: SessionState) -> dict:
    """Enrich each candidate with a fuller summary before selection, so the
    LLM picks from real paragraphs instead of 300-char feed snippets."""
    enriched = []
    for c in state["candidates"]:
        try:
            summary = curriculum.fetch_article_extract(
                c["title"], fallback_extract=c["extract"], max_chars=CANDIDATE_SUMMARY_CHARS)
        except requests.RequestException:
            summary = c["extract"]
        enriched.append({**c, "extract": summary[:CANDIDATE_SUMMARY_CHARS]})
    return {"candidates": enriched}


def select_article_node(state: SessionState) -> dict:
    try:
        return {"article": curriculum.select_article(state["candidates"], state["profile"])}
    except requests.RequestException as e:
        return {"setup_failed": f"Ollama unavailable during article selection: {e}"}


def fetch_article_details_node(state: SessionState) -> dict:
    article = state["article"]
    try:
        extract = curriculum.fetch_article_extract(
            article["title"], fallback_extract=article["extract"])
    except requests.RequestException:
        extract = article["extract"]
    return {"article_extract": extract}


def generate_story_node(state: SessionState) -> dict:
    profile = state["profile"]
    try:
        story = curriculum.generate_story(
            state["article"]["title"], state["article_extract"], profile)
        return {"story": story, "practice_words": curriculum.top_vocab_to_practice(profile)}
    except (requests.RequestException, ValueError, KeyError) as e:
        return {"setup_failed": f"story generation failed: {e}"}


def save_session_files_node(state: SessionState) -> dict:
    session_dir = Path(state["session_dir"])
    article = state["article"]
    story = state["story"]
    curriculum.save_session_doc(session_dir, "article.md", article["title"],
                                 f"**Source:** {article['source']}\n\n{state['article_extract']}")
    curriculum.save_session_doc(session_dir, "story.md", story["title"], story["story"])
    return {}


def record_article_covered_node(state: SessionState) -> dict:
    """Eager profile write — even if the user quits before speaking, today's
    article and any practiced vocab are already recorded so tomorrow's
    session doesn't repeat them."""
    profile = state["profile"]
    session_name = Path(state["session_dir"]).name
    curriculum.record_article_covered(profile, state["article"]["title"], session_name)
    curriculum.bump_vocab_targeted(profile, state.get("practice_words", []))
    curriculum.save_profile(profile)
    return {"profile": profile}


def _skip_on_failure(next_node: str):
    def route(state: SessionState) -> str:
        return END if state.get("setup_failed") else next_node
    return route


def _build_setup_graph():
    g = StateGraph(SessionState)
    g.add_node("LoadProfile", load_profile_node)
    g.add_node("FetchFeaturedFeed", fetch_featured_feed_node)
    g.add_node("ExtractCandidates", extract_candidates_node)
    g.add_node("SelectArticle", select_article_node)
    g.add_node("FetchArticleDetails", fetch_article_details_node)
    g.add_node("GenerateStory", generate_story_node)
    g.add_node("SaveSessionFiles", save_session_files_node)
    g.add_node("RecordArticleCovered", record_article_covered_node)

    g.add_edge(START, "LoadProfile")
    g.add_edge("LoadProfile", "FetchFeaturedFeed")
    g.add_conditional_edges("FetchFeaturedFeed", _skip_on_failure("ExtractCandidates"),
                            ["ExtractCandidates", END])
    g.add_edge("ExtractCandidates", "SelectArticle")
    g.add_conditional_edges("SelectArticle", _skip_on_failure("FetchArticleDetails"),
                            ["FetchArticleDetails", END])
    g.add_edge("FetchArticleDetails", "GenerateStory")
    g.add_conditional_edges("GenerateStory", _skip_on_failure("SaveSessionFiles"),
                            ["SaveSessionFiles", END])
    g.add_edge("SaveSessionFiles", "RecordArticleCovered")
    g.add_edge("RecordArticleCovered", END)
    return g.compile()


# ---------------------------------------------------------------------------
# Session analysis graph
# ---------------------------------------------------------------------------

def analyze_transcript_node(state: SessionState) -> dict:
    # Recurring weaknesses are read BEFORE today's findings are merged, so
    # homework targets past persistent issues without double-counting today.
    out = {"recurring": curriculum.top_weaknesses(state["profile"]), "analysis": None}
    try:
        analysis = curriculum.analyze_weaknesses(state["transcript_text"])
        (Path(state["session_dir"]) / "analysis.json").write_text(
            json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
        out["analysis"] = analysis
    except (requests.RequestException, ValueError) as e:
        print(f"Analysis unavailable ({e}); homework will use the raw transcript.")
    return out


def generate_homework_node(state: SessionState) -> dict:
    story = state.get("story")
    try:
        homework = curriculum.generate_homework(
            state.get("analysis"), state["transcript_text"],
            story["title"] if story else None, state["recurring"])
        return {"homework": homework}
    except requests.RequestException as e:
        print(f"Could not generate homework (Ollama error): {e}")
        return {"homework": None}


def update_student_profile_node(state: SessionState) -> dict:
    profile = state["profile"]
    curriculum.merge_analysis_into_profile(profile, state.get("analysis"))
    curriculum.save_profile(profile)
    return {"profile": profile}


def write_homework_node(state: SessionState) -> dict:
    if state.get("homework"):
        session_dir = Path(state["session_dir"])
        path = curriculum.save_session_doc(
            session_dir, "homework.md", f"Homework — {session_dir.name}", state["homework"])
        print(f"Homework saved to {path}")
    return {}


def _build_analysis_graph():
    g = StateGraph(SessionState)
    g.add_node("AnalyzeTranscript", analyze_transcript_node)
    g.add_node("GenerateHomework", generate_homework_node)
    g.add_node("UpdateStudentProfile", update_student_profile_node)
    g.add_node("WriteHomework", write_homework_node)

    g.add_edge(START, "AnalyzeTranscript")
    g.add_edge("AnalyzeTranscript", "GenerateHomework")
    g.add_edge("GenerateHomework", "UpdateStudentProfile")
    g.add_edge("UpdateStudentProfile", "WriteHomework")
    g.add_edge("WriteHomework", END)
    return g.compile()


session_setup_graph = _build_setup_graph()
session_analysis_graph = _build_analysis_graph()
