"""Low-level Ollama HTTP client shared by the conversation graph and main.py."""
import requests

OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_URL = "http://localhost:11434/api/chat"


def chat_completion(messages: list, json_mode: bool = False) -> str:
    """Call the Ollama chat API and return the assistant's text.

    json_mode=True asks Ollama to constrain the output to valid JSON,
    which the analyze node relies on for its verdict.
    """
    payload = {"model": OLLAMA_MODEL, "messages": messages, "stream": False}
    if json_mode:
        payload["format"] = "json"
    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    response.raise_for_status()
    return response.json()["message"]["content"]
