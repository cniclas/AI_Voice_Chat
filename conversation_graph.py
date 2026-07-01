"""LangGraph flow for one conversation turn.

Each user utterance runs through:

    START -> analyze -> respond -> END
                    \-> clarify -> END

- analyze: asks the LLM whether the (Whisper-transcribed) utterance is
  coherent enough to answer. Learner mistakes are fine; only garbled or
  nonsensical transcriptions count as "not understood".
- respond: the normal tutor reply, appended to the chat history.
- clarify: a short question asking the student to repeat/rephrase. Capped
  at MAX_CONSECUTIVE_CLARIFICATIONS so the tutor never gets stuck nagging.
"""
import json
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

import llm

# After this many clarification requests in a row, answer best-effort instead
# of asking again.
MAX_CONSECUTIVE_CLARIFICATIONS = 2

# How many recent conversation messages the analyze node sees for context.
ANALYZE_CONTEXT_MESSAGES = 6

ANALYZE_SYSTEM_PROMPT = (
    "You are a filter in a voice chat between a Spanish student and a tutor. "
    "The student's speech is transcribed automatically, so it sometimes arrives "
    "garbled, cut off, or as random words. Decide whether the tutor can "
    "reasonably understand and answer the utterance.\n\n"
    "IMPORTANT: grammar mistakes, wrong vocabulary, mixed English/Spanish and "
    "broken sentences are NORMAL for a learner and count as understandable. "
    "Only mark an utterance as not understood when it is incoherent noise, an "
    "obvious mis-transcription, or has no discernible intent even given the "
    "conversation context.\n\n"
    'Reply with JSON only: {"understood": true} or {"understood": false}.'
)

CLARIFY_SYSTEM_PROMPT = (
    "Eres un profesor de español amable. No entendiste bien lo último que dijo "
    "el estudiante (la transcripción salió confusa). Pídele con mucha amabilidad "
    "que lo repita o lo diga de otra manera, en una o dos frases cortas en "
    "español sencillo. No intentes adivinar lo que quiso decir."
)


class ChatState(TypedDict):
    language: str        # "en" or "es" — language the user recorded in
    user_text: str       # transcribed utterance for this turn
    messages: list       # full LLM chat history (system prompt + turns)
    understood: bool     # verdict from the analyze node
    clarify_count: int   # consecutive clarification requests so far
    response_text: str   # assistant reply produced this turn
    asked_clarification: bool  # True when the reply is a clarification request


def analyze(state: ChatState) -> dict:
    """Ask the LLM whether the utterance is coherent enough to answer."""
    context = [m for m in state["messages"] if m["role"] != "system"]
    context = context[-ANALYZE_CONTEXT_MESSAGES:]
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in context)
    prompt = (
        f"Recent conversation:\n{transcript or '(start of conversation)'}\n\n"
        f"New student utterance (spoken in {state['language']}):\n"
        f"{state['user_text']}"
    )
    try:
        raw = llm.chat_completion(
            [
                {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            json_mode=True,
        )
        understood = bool(json.loads(raw).get("understood", True))
    except (json.JSONDecodeError, TypeError, AttributeError):
        # Fail open: better to answer best-effort than to nag the student.
        understood = True
    return {"understood": understood}


def route_after_analyze(state: ChatState) -> str:
    if state["understood"]:
        return "respond"
    if state["clarify_count"] >= MAX_CONSECUTIVE_CLARIFICATIONS:
        return "respond"
    return "clarify"


def respond(state: ChatState) -> dict:
    """Normal tutor reply; the turn becomes part of the chat history."""
    messages = state["messages"] + [{"role": "user", "content": state["user_text"]}]
    assistant_text = llm.chat_completion(messages)
    messages.append({"role": "assistant", "content": assistant_text})
    return {
        "messages": messages,
        "response_text": assistant_text,
        "clarify_count": 0,
        "asked_clarification": False,
    }


def clarify(state: ChatState) -> dict:
    """Ask the student to repeat/rephrase; also recorded in the history."""
    question = llm.chat_completion(
        [
            {"role": "system", "content": CLARIFY_SYSTEM_PROMPT},
            {"role": "user", "content": state["user_text"]},
        ]
    )
    messages = state["messages"] + [
        {"role": "user", "content": state["user_text"]},
        {"role": "assistant", "content": question},
    ]
    return {
        "messages": messages,
        "response_text": question,
        "clarify_count": state["clarify_count"] + 1,
        "asked_clarification": True,
    }


def build_conversation_graph():
    """Compile the analyze -> respond/clarify graph for a single turn."""
    graph = StateGraph(ChatState)
    graph.add_node("analyze", analyze)
    graph.add_node("respond", respond)
    graph.add_node("clarify", clarify)
    graph.add_edge(START, "analyze")
    graph.add_conditional_edges(
        "analyze", route_after_analyze, {"respond": "respond", "clarify": "clarify"}
    )
    graph.add_edge("respond", END)
    graph.add_edge("clarify", END)
    return graph.compile()
