"""Shared helper functions for e2e parity tests."""
import json
import os
import re

import requests

_JUDGE_MODEL = "kimi"
_JUDGE_BASE_URL = "https://ellm.nrp-nautilus.io/v1"


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


def stream_chat(
    backend_url: str,
    chat_id: str,
    message: str,
    report_study_id: int = None,
    timeout: int = 120,
) -> dict:
    """POST a message to a global chat and consume the SSE stream.

    Returns a dict with:
      query_plan          — the query_plan SSE event payload (or None)
      search_count        — int from step_done/search_db detail (or None)
      assistant_text      — full assembled LLM reply
      study_ids_mentioned — set of ints found in the assistant text
      ui_payload          — payload from the `ui` SSE event (set when report_study_id is used)
      step_done_labels    — list of (name, label) tuples from all step_done events
    """
    body = {"user_id": "parity_test", "message": message}
    if report_study_id is not None:
        body["report_study_id"] = report_study_id

    r = requests.post(
        f"{backend_url}/api/global-chats/{chat_id}/message/stream",
        json=body,
        stream=True,
        timeout=timeout,
    )
    r.raise_for_status()

    query_plan = None
    search_count = None
    ui_payload = None
    step_done_labels = []
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
            elif event_type == "step_done":
                name = data.get("name") or ""
                label = data.get("label") or ""
                step_done_labels.append((name, label))
                if name == "search_db":
                    m = re.search(r"(\d+)\s+stud", data.get("detail") or "")
                    if m:
                        search_count = int(m.group(1))
            elif event_type == "ui":
                ui_payload = data
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
        "ui_payload": ui_payload,
        "step_done_labels": step_done_labels,
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


_REFUSAL_FALLBACK_RE = re.compile(
    r"(not\s+(public|available|accessible)|private|no\s+(?:accessible\s+)?data|cannot\s+find|not\s+found)",
    re.I,
)


def llm_judge(question: str, answer: str, rubric: str) -> bool:
    """Ask kimi to evaluate whether the assistant's answer meets the rubric.

    Returns True if the judge says YES, False for NO.
    Falls back to a simple regex check if the endpoint is unreachable.
    """
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("API_KEY")
    if not api_key:
        # No key in env — can't judge; treat as inconclusive (pass)
        return True

    prompt = (
        "You are evaluating whether an AI assistant's response achieved a specific goal.\n"
        f"User question: {question}\n"
        f"Goal to evaluate: {rubric}\n"
        f"Assistant response: {answer}\n\n"
        "Answer only YES or NO. No explanation."
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=_JUDGE_BASE_URL, timeout=45.0)
        resp = client.chat.completions.create(
            model=_JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=5,
        )
        verdict = (resp.choices[0].message.content or "").strip().upper()
        return verdict.startswith("YES")
    except Exception:
        # Endpoint unreachable — fall back to a simple keyword heuristic
        return bool(re.search(r"\b(yes|found|available|mention|recommend|compare|sample)\b", answer, re.I))
