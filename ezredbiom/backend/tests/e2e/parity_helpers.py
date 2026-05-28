"""Shared helper functions for e2e parity tests."""
import json
import re

import requests

_REFUSAL_RE = re.compile(
    r"(not\s+(public|available|accessible)|private|no\s+(?:accessible\s+)?data|cannot\s+find|not\s+found)",
    re.I,
)


def search_ids(backend_url: str, query: str) -> set:
    """POST /api/search and return set of study_id ints from results."""
    r = requests.post(
        f"{backend_url}/api/search",
        json={"query": query},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return {int(row["study_id"]) for row in (data.get("results") or [])}


def stream_chat(backend_url: str, chat_id: str, message: str, timeout: int = 90) -> dict:
    """POST a message to a global chat and consume the SSE stream.

    Returns a dict with:
      query_plan          — the query_plan SSE event payload (or None)
      search_count        — int from step_done search_db detail (or None)
      assistant_text      — full assembled LLM reply
      study_ids_mentioned — set of ints found in the assistant text
    """
    r = requests.post(
        f"{backend_url}/api/global-chats/{chat_id}/message/stream",
        json={"user_id": "parity_test", "message": message},
        stream=True,
        timeout=timeout,
    )
    r.raise_for_status()

    query_plan = None
    search_count = None
    tokens = []

    for raw_line in r.iter_lines(decode_unicode=True):
        if not raw_line or raw_line.startswith(":"):
            continue
        if raw_line.startswith("data:"):
            payload_str = raw_line[5:].strip()
            try:
                event_data = json.loads(payload_str)
            except json.JSONDecodeError:
                continue
            event_type = event_data.get("type")
            data = event_data.get("data") or {}

            if event_type == "query_plan":
                query_plan = data
            elif event_type == "step_done" and data.get("name") == "search_db":
                detail = data.get("detail") or ""
                m = re.search(r"(\d+)\s+stud", detail)
                if m:
                    search_count = int(m.group(1))
            elif event_type == "token":
                tokens.append(data.get("token") or "")

    assistant_text = "".join(tokens).strip()
    mentioned = set(
        int(x)
        for x in re.findall(r"\b(?:study\s+#?)?(\d{4,6})\b", assistant_text, re.I)
    )

    return {
        "query_plan": query_plan,
        "search_count": search_count,
        "assistant_text": assistant_text,
        "study_ids_mentioned": mentioned,
    }


def chat_search_ids(backend_url: str, query_plan: dict) -> set:
    """Re-run the chat's search step via /api/search using its planner keywords.

    Mirrors build_where_from_plan → search_studies_with_sql, but over HTTP
    so no direct Postgres connection is required in the caller.
    """
    keywords = query_plan.get("keywords") or []
    if not keywords:
        return set()
    query = " ".join(keywords[:10])
    return search_ids(backend_url, query)


def text_is_refusal(text: str) -> bool:
    return bool(_REFUSAL_RE.search(text))
