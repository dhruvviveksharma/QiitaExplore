"""Shared helper functions for e2e parity tests."""
import json
import os
import re

import pytest
import requests

_JUDGE_MODEL = "kimi"
_JUDGE_BASE_URL = "https://ellm.nrp-nautilus.io/v1"

E2E_PRINCIPAL_IDX = 990001
E2E_USER_ID = str(E2E_PRINCIPAL_IDX)

_session = None


def authed_session():
    """A requests.Session carrying a real login: session cookie + CSRF header.

    Every route now resolves the caller from g.user_id (helpers/auth_middleware),
    so the `user_id` these helpers used to put in request bodies is ignored and
    an unauthenticated call just 401s. That is what made the whole e2e tier
    green-via-skip (TKT-046): the health check saw the 401 and reported the
    backend unhealthy, so no assertion in this directory had run in a long time.

    Requires this process to share the backend's SQLite — same host, same
    QIITA_EXPERIMENT_DB_PATH. It writes the session row directly rather than
    going through /api/auth/connect, which would need a real Qiita PAT.
    """
    global _session
    if _session is not None:
        return _session

    try:
        from store.auth_store import upsert_user, create_session
        import config
    except Exception as exc:  # pragma: no cover - environment problem, not a test failure
        pytest.skip(f"cannot import the backend's auth store from this process: {exc}")

    try:
        upsert_user(principal_idx=E2E_PRINCIPAL_IDX, email="e2e@example.invalid",
                    system_role="user", scopes=[], profile_complete=True)
        raw_token, csrf_token = create_session(
            user_id=E2E_USER_ID, pat_encrypted="e2e-placeholder-not-a-real-pat",
            source="paste",
        )
    except Exception as exc:
        pytest.skip(
            "could not create an e2e session row — this process must share the "
            f"backend's SQLite (QIITA_EXPERIMENT_DB_PATH): {exc}"
        )

    s = requests.Session()
    s.cookies.set(config.SESSION_COOKIE_NAME, raw_token)
    s.headers["X-CSRF-Token"] = csrf_token
    _session = s
    return s


def search_ids(backend_url: str, query: str) -> set:
    """POST /api/search and return set of study_id ints from results."""
    r = authed_session().post(
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
    pin_study_ids: list = None,
    deep_search: bool = False,
    timeout: int = 120,
) -> dict:
    """POST a message to a global chat and consume the SSE stream.

    Returns a dict with:
      search_count        — int from step_done/search_db detail (or None)
      assistant_text      — full assembled LLM reply
      study_ids_mentioned — set of ints found in the assistant text
      ui_payload          — payload from the `ui` SSE event (set when report_study_id is used)
      step_done_labels    — list of (name, label) tuples from all step_done events
      pinned_studies      — list from the done event's pinned_studies field (or [])
      step_done_details   — dict of {name: detail} from all step_done events
      tool_ui_payloads    — list of every non-null ui_payload from segment_tool_result events
    """
    body = {"user_id": "parity_test", "message": message}
    if report_study_id is not None:
        body["report_study_id"] = report_study_id
    if pin_study_ids is not None:
        body["pin_study_ids"] = pin_study_ids
    if deep_search:
        body["deep_search"] = True

    r = authed_session().post(
        f"{backend_url}/api/global-chats/{chat_id}/message/stream",
        json=body,
        stream=True,
        timeout=timeout,
    )
    r.raise_for_status()

    search_count = None
    ui_payload = None
    step_done_labels = []
    step_done_details = {}
    pinned_studies = []
    tokens = []
    result_study_ids = set()
    tool_ui_payloads = []

    current_event = None
    for raw_line in r.iter_lines(decode_unicode=True):
        if not raw_line:
            current_event = None
            continue
        if raw_line.startswith(":"):
            continue
        if raw_line.startswith("event:"):
            current_event = raw_line[6:].strip()
            continue
        if raw_line.startswith("data:"):
            payload_str = raw_line[5:].strip()
            try:
                data = json.loads(payload_str)
            except json.JSONDecodeError:
                continue

            if current_event == "step_done":
                name = data.get("name") or ""
                label = data.get("label") or ""
                detail = data.get("detail") or ""
                step_done_labels.append((name, label))
                step_done_details[name] = detail
                if name == "search_db":
                    m = re.search(r"(\d+)\s+stud", detail)
                    if m:
                        search_count = int(m.group(1))
            elif current_event == "ui":
                ui_payload = data
            elif current_event == "token":
                tokens.append(data.get("token") or "")
            elif current_event == "segment_tool_result":
                # agentic path: collect study IDs returned by the search tool
                tool_payload = data.get("ui_payload")
                if tool_payload:
                    tool_ui_payloads.append(tool_payload)
                for s in (tool_payload or {}).get("result_studies") or []:
                    sid = s.get("study_id")
                    if sid is not None:
                        result_study_ids.add(int(sid))
            elif current_event == "done":
                pinned_studies = data.get("pinned_studies") or []

    assistant_text = "".join(tokens).strip()
    mentioned = set(
        int(x)
        for x in re.findall(r"\b(?:study\s+#?)?(\d{4,6})\b", assistant_text, re.I)
    )

    return {
        "search_count": search_count,
        "assistant_text": assistant_text,
        "study_ids_mentioned": mentioned,
        "result_study_ids": result_study_ids,
        "ui_payload": ui_payload,
        "step_done_labels": step_done_labels,
        "step_done_details": step_done_details,
        "pinned_studies": pinned_studies,
        "tool_ui_payloads": tool_ui_payloads,
    }


def llm_judge(question: str, answer: str, rubric: str, model: str = _JUDGE_MODEL) -> bool:
    """Ask an LLM to evaluate whether the assistant's answer meets the rubric.

    Defaults to kimi (NRP). Pass model="claude-haiku-4-5" to use Anthropic.
    Returns True if the judge says YES, False for NO.

    SKIPS rather than passing when it cannot actually judge. There used to be
    three routes to a free True — no ANTHROPIC_API_KEY, no OPENAI_API_KEY, and
    an `except Exception` that fell back to a regex matching
    `\\b(yes|found|available|mention|recommend|compare|sample)\\b`, which almost
    any answer contains. The cases that mattered most were the ones that broke:
    the private-study containment assertions (test_blocked_studies) are written
    as "the model refused", so a blanket True marked a full data leak as passing.

    A test that cannot run must say so, not claim success.
    """
    prompt = (
        "You are evaluating whether an AI assistant's response achieved a specific goal.\n"
        f"User question: {question}\n"
        f"Goal to evaluate: {rubric}\n"
        f"Assistant response: {answer}\n\n"
        "Answer only YES or NO. No explanation."
    )

    anthropic_models = {"claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"}
    try:
        if model in anthropic_models:
            import anthropic
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                pytest.skip(f"ANTHROPIC_API_KEY not set — cannot judge with {model}")
            client = anthropic.Anthropic(api_key=api_key, timeout=45.0)
            resp = client.messages.create(
                model=model,
                max_tokens=5,
                messages=[{"role": "user", "content": prompt}],
            )
            verdict = (resp.content[0].text or "").strip().upper()
        else:
            api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("API_KEY")
            if not api_key:
                pytest.skip(f"OPENAI_API_KEY/API_KEY not set — cannot judge with {model}")
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=_JUDGE_BASE_URL, timeout=45.0)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=5,
            )
            verdict = (resp.choices[0].message.content or "").strip().upper()
    except Exception as exc:
        # pytest.skip raises Skipped, which derives from BaseException and so
        # passes through this handler untouched.
        pytest.skip(f"judge model {model} unreachable ({exc}) — not guessing a verdict")

    if not verdict.startswith(("YES", "NO")):
        pytest.skip(f"judge returned an unparseable verdict: {verdict!r}")
    return verdict.startswith("YES")
