"""LLM context builders, SSE formatter, and streaming wrappers."""

import json

import anthropic as _anthropic

from config import (
    get_client,
    CHAT_SYSTEM_PROMPT,
    DEFAULT_MODEL,
    ALLOWED_MODELS,
)


def _resolve_model(model):
    if model and model in ALLOWED_MODELS:
        return model
    return DEFAULT_MODEL


def friendly_llm_error(exc, model=None):
    # Checked before the connection markers below: a dead sidecar raises
    # "sidecar unreachable: connection refused", which would otherwise match
    # those markers and advise switching model — useless, since every model
    # routes through the same sidecar. Imported here rather than at module
    # scope because helpers.pi_client imports config, which imports this module.
    from helpers.pi_client import PiSidecarError
    if isinstance(exc, PiSidecarError):
        return "The chat service is not responding. This is a backend problem, not a model one — switching models will not help."
    if isinstance(exc, _anthropic.RateLimitError):
        return f"{model or 'Claude'} rate limit reached. Please wait a moment and try again."
    if isinstance(exc, (_anthropic.APIConnectionError, _anthropic.APIStatusError)):
        return f"{model or 'Claude'} is currently unavailable. Check your ANTHROPIC_API_KEY and try again."
    raw = str(exc) or exc.__class__.__name__
    lowered = raw.lower()
    connection_markers = (
        "upstream connect error",
        "connection refused",
        "remote connection failure",
        "delayed connect error",
        "connection reset",
        "service unavailable",
        "502", "503", "504",
    )
    if any(m in lowered for m in connection_markers):
        name = model or "the selected model"
        return f"{name} is currently unavailable on NRP-Nautilus. Try selecting a different model from the dropdown below the chat box."
    return raw


def _extract_system_and_messages(api_msgs):
    """Split the system message from conversation messages for Anthropic's separate system param."""
    system = ""
    msgs = []
    for m in api_msgs:
        if m.get("role") == "system":
            system = m.get("content") or ""
        else:
            msgs.append(m)
    return system, msgs


def _sse(event: str, payload: dict):
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _normalize_messages(messages):
    trimmed = messages[-10:] if len(messages) > 10 else list(messages)
    out = []
    for m in trimmed:
        role = m.get("role") or "user"
        if role not in ("user", "assistant"):
            role = "user"
        content = (m.get("content") or "").strip()
        out.append({"role": role, "content": content})
    return out


def _truncate(value, limit):
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _format_pi_line(pi_name, pi_affiliation) -> str:
    """'Noah Fierer (University of Colorado)', degrading to the name alone, the
    affiliation alone, or 'N/A' as fields go missing.

    Shared so the three places that show a study to the model agree: search
    results (_study_discovery_compact_block), the report block
    (qiita_fetch._build_full_samples_block), and the project workspace manifest.
    They did not agree before — only search carried the PI, so a model that had
    read a study report would state, correctly for its own context and wrongly
    for the user, that no PI information existed."""
    pi  = _truncate(pi_name or "N/A", 80)
    aff = _truncate((pi_affiliation or "").strip(), 80)
    if not aff:
        return pi
    return f"{pi} ({aff})" if pi != "N/A" else aff


def _study_discovery_compact_block(study: dict) -> str:
    """One study, minimal lines for global discovery (no sample metadata dump)."""
    sid   = study.get("study_id")
    title = _truncate(study.get("study_title") or "Untitled study", 140)
    pi_line = _format_pi_line(study.get("pi_name"), study.get("pi_affiliation"))
    abstract = _truncate((study.get("study_abstract") or "").strip() or "Not available", 600)
    dt       = _truncate((study.get("data_types") or "").strip(), 80)
    ns       = study.get("num_samples")
    np       = study.get("num_preps")
    counts   = []
    if ns is not None:
        counts.append(f"{ns} samples")
    if np is not None:
        counts.append(f"{np} preps")
    count_s = " · ".join(counts) if counts else "counts n/a"
    dtype_s = f" | Types: {dt}" if dt else ""
    return (
        f"- ID {sid}: {title}\n"
        f"  PI: {pi_line} | {count_s}{dtype_s}\n"
        f"  Abstract: {abstract}"
    )


def _format_discovery_study_list(studies, header_line: str, max_chars: int):
    """Fit as many compact study blocks as possible under max_chars.

    Appends "(showing N of M — context budget)" whenever fewer blocks fit than
    were asked for. Callers build header_line from len(studies) BEFORE knowing
    what fits, so without this the header asserts a count the body does not
    contain — and the model believes it. That is exactly what produced a reply
    reading "the system notes that 2 studies were selected, but only Study 393's
    details appear": correct observation, and the only reason it could be made
    was that the model noticed the contradiction itself.

    Still `continue` rather than `break`: packing a later, smaller block in is
    the right call for search results, where the list is ranked but any hit is
    useful. What was wrong was staying silent about it.
    """
    if not studies:
        return f"{header_line}\n(none)\n"
    chosen  = []
    running = len(header_line) + 2
    for s in studies:
        block = _study_discovery_compact_block(s)
        gap   = 0 if not chosen else 2
        if running + gap + len(block) > max_chars:
            continue
        running += gap + len(block)
        chosen.append(block)
    if len(chosen) < len(studies):
        header_line = f"{header_line.rstrip()} (showing {len(chosen)} of {len(studies)} — context budget)"
    out = header_line.strip() + "\n\n" + "\n\n".join(chosen) if chosen else header_line.strip() + "\n"
    return out + "\n"


def _build_api_messages(messages, study_context_text: str, system_prompt: str = None):
    prompt = system_prompt or CHAT_SYSTEM_PROMPT
    if study_context_text:
        context_block = f"\n\nSTUDY CONTEXT:\n{study_context_text}"
    else:
        context_block = (
            "\n\nSTUDY CONTEXT:\n"
            "No study records were provided for this request. Do not list specific studies."
        )
    system_content = prompt + context_block
    return [{"role": "system", "content": system_content}] + _normalize_messages(messages)


def merge_global_chat_context(selected_studies, user_query: str, budget: int = 24_000) -> str:
    """Build LLM context from the user-selected browse chips for global chat."""
    selected = selected_studies or []

    intro = (
        "Context layout: USER-SELECTED BROWSE CONTEXT — studies the user attached as chips. "
        "Reference them when the question is specifically about those IDs or for comparison.\n"
    )
    # Derived from the budget the caller computed for this model, not hardcoded.
    # This line used to read `min(14000, max(1500, 400 * len(selected)))`, which
    # never looked at `budget` at all: on qwen3, where context_budget_chars gives
    # 3,507,000 chars, two attached studies were allowed 1,500 — and a real
    # compact block measures ~774 (up to ~950 once _truncate's 600-char abstract
    # cap is hit), so the second one silently did not fit. Broken for EVERY count
    # >= 2, not an edge case.
    #
    # 1,000 per study covers a full block with headroom; the 8,000 floor keeps one
    # or two chips generous; capped at the caller's own budget so it can never
    # ask for more room than the model actually has.
    sel_budget = min(max(1_000 * len(selected), 8_000), budget)

    sel_header = f"USER-SELECTED BROWSE CONTEXT ({len(selected)} studies):"
    sel_text   = _format_discovery_study_list(selected, sel_header, sel_budget)

    return intro.strip() + "\n\n" + sel_text.strip()


def llm_chat_stream(messages, study_context_text: str, system_prompt: str = None, model: str = None):
    resolved = _resolve_model(model)
    llm_client, provider = get_client(resolved)
    api_msgs = _build_api_messages(messages, study_context_text, system_prompt)
    if provider == "anthropic":
        system, msgs = _extract_system_and_messages(api_msgs)
        with llm_client.messages.stream(model=resolved, max_tokens=4096, system=system, messages=msgs) as stream:
            yielded = False
            for text in stream.text_stream:
                yield text
                yielded = True
        if not yielded:
            yield "(No response received from model)"
        return
    stream = llm_client.chat.completions.create(model=resolved, messages=api_msgs, stream=True)
    yielded = False
    for chunk in stream:
        if not chunk.choices:
            continue
        token = chunk.choices[0].delta.content
        if token:
            yield token
            yielded = True
    if not yielded:
        yield "(No response received from model)"


def _build_workspace_manifest(proj) -> str:
    """Short 'what's in this workspace' list — one line per study (id, title,
    data types). Sent as part of context_block (per-turn, never persisted to
    the pi session) rather than system_prompt, since pi's system prompt is
    fixed for the session's lifetime but workspace membership can change
    between turns. The workspace-scoped search_studies tool
    (helpers/project_scope.py) and pi's own compaction handle everything
    beyond this short manifest."""
    studies = (proj or {}).get("studies") or []
    if not studies:
        return "This project's workspace currently has no studies added."
    lines = [f"This project's workspace contains {len(studies)} studies:"]
    for s in studies:
        title = (s.get("study_title") or "Untitled study").strip()
        dtypes = s.get("data_types") or "unknown"
        # PI included so workspace-wide questions about investigators are
        # answerable without a tool call at all. project_studies already stores
        # pi_name/pi_affiliation (store/db.py, selected in crud.py), so this is
        # a read of rows already loaded — no extra query, ~25 chars per study.
        pi = _format_pi_line(s.get("pi_name"), s.get("pi_affiliation"))
        lines.append(f"- Study {s.get('study_id')}: {title} ({dtypes}) | PI: {pi}")
    return "\n".join(lines)
