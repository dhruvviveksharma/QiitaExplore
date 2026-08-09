"""LLM context builders, SSE formatter, and streaming wrappers."""

import json
import re

import anthropic as _anthropic

from config import (
    client,
    get_client,
    DEFAULT_MODEL,
    ALLOWED_MODELS,
)
from store import (
    get_study_detail_cache,
    upsert_study_detail_cache,
)


def _resolve_model(model):
    if model and model in ALLOWED_MODELS:
        return model
    return DEFAULT_MODEL


def friendly_llm_error(exc, model=None):
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


_STUDY_BLOCK_SKIP_KEYS = {
    "study_id", "study_title", "study_abstract", "pi_name", "pi_email",
    "pi_affiliation", "lab_person_name", "summary_text", "added_at", "updated_at",
    "study_alias", "metadata_complete", "data_types", "num_samples", "num_preps",
    "preps_json", "samples_context",
}


def _study_detail_block(study: dict, include_samples_context: bool = True):
    sid = study.get("study_id")
    title         = _truncate(study.get("study_title") or "Untitled study", 160)
    abstract      = _truncate(study.get("study_abstract") or "Not available", 900)
    pi_name       = _truncate(study.get("pi_name") or "Not available", 120)
    pi_email      = _truncate(study.get("pi_email") or "Not available", 140)
    pi_affiliation= _truncate(study.get("pi_affiliation") or "Not available", 200)
    lab_person    = _truncate(study.get("lab_person_name") or "Not available", 140)

    enriched_lines = []
    data_types  = (study.get("data_types") or "").strip()
    num_samples = study.get("num_samples")
    num_preps   = study.get("num_preps")
    preps_json  = study.get("preps_json") or "[]"

    if data_types:
        enriched_lines.append(f"  Data Types: {data_types}")
    if num_samples is not None:
        enriched_lines.append(f"  Num Samples: {num_samples}")
    if num_preps is not None:
        enriched_lines.append(f"  Num Preps: {num_preps}")
    if preps_json and preps_json != "[]":
        try:
            preps = json.loads(preps_json)
            for p in preps[:5]:
                prep_id  = p.get("prep_template_id", "?")
                dtype    = p.get("data_type", "?")
                inv_type = p.get("investigation_type") or "N/A"
                status   = p.get("preprocessing_status") or "N/A"
                enriched_lines.append(f"    Prep {prep_id}: {dtype} | {inv_type} | {status}")
        except Exception:
            pass

    extra_lines = []
    for key, value in study.items():
        if key in _STUDY_BLOCK_SKIP_KEYS:
            continue
        if value is None:
            continue
        val = _truncate(value, 180)
        if not val:
            continue
        extra_lines.append(f"  {key}: {val}")

    samples_context = (study.get("samples_context") or "").strip()
    if include_samples_context and samples_context:
        samples_tail = f"\n{_truncate(samples_context, 3500)}"
    else:
        samples_tail = ""

    return (
        f"- ID {sid}: {title}\n"
        f"  Abstract: {abstract}\n"
        f"  PI: {pi_name}\n"
        f"  PI Email: {pi_email}\n"
        f"  PI Affiliation: {pi_affiliation}\n"
        f"  Lab Contact: {lab_person}"
        + (f"\n{chr(10).join(enriched_lines)}" if enriched_lines else "")
        + (f"\n{chr(10).join(extra_lines)}" if extra_lines else "")
        + samples_tail
    )


def _study_discovery_compact_block(study: dict) -> str:
    """One study, minimal lines for global discovery (no sample metadata dump)."""
    sid   = study.get("study_id")
    title = _truncate(study.get("study_title") or "Untitled study", 140)
    pi    = _truncate(study.get("pi_name") or "N/A", 80)
    aff   = _truncate((study.get("pi_affiliation") or "").strip(), 80)
    if aff:
        pi_line = f"{pi} ({aff})" if pi != "N/A" else aff
    else:
        pi_line = pi
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


def _format_discovery_study_list(studies, header_line: str, max_chars: int,
                                 *, tools_available: bool = True,
                                 report_tool_name: str = "get_study_report"):
    """Fit as many compact study blocks as possible under max_chars.

    Studies that don't fit are listed as one-line stubs rather than dropped:
    without its ID a study is invisible to the model. Stubs are ~70 chars and sit
    outside max_chars, which governs the full blocks.

    `tools_available` reflects the CALLER's execution mode, not this renderer's:
    pointing a model at get_study_report when the request carries no tools is an
    instruction it cannot follow.
    """
    if not studies:
        return f"{header_line}\n(none)\n"
    chosen  = []
    running = len(header_line) + 2
    for s in studies:
        block = _study_discovery_compact_block(s)
        gap   = 0 if not chosen else 2
        # break, not continue: keep relevance order rather than letting a later,
        # smaller study leapfrog one that was too big.
        if running + gap + len(block) > max_chars:
            break
        running += gap + len(block)
        chosen.append(block)
    out = header_line.strip() + "\n\n" + "\n\n".join(chosen) if chosen else header_line.strip() + "\n"
    omitted = studies[len(chosen):]
    if omitted:
        hatch = f" — call {report_tool_name}(<id>) for any of them" if tools_available else ""
        out += f"\n\n({len(omitted)} more not shown in full{hatch}:)\n"
        out += "\n".join(
            f"- ID {s.get('study_id')}: {_truncate(s.get('study_title') or 'Untitled study', 140)}"
            for s in omitted
        )
    return out + "\n"


def _build_project_study_context(project: dict, budget: int = 12_000):
    if not project:
        return None
    studies = project.get("studies") or []
    if not studies:
        return None

    # Lazy-import to avoid circular dependency at module load time
    from helpers.qiita_fetch import _fetch_sample_context_text

    for s in studies:
        sid = s.get("study_id")
        if sid and not s.get("samples_context"):
            cached_detail = get_study_detail_cache(sid)
            if cached_detail and cached_detail.get("samples_context"):
                s["samples_context"] = cached_detail["samples_context"]
            else:
                ctx = _fetch_sample_context_text(sid)
                if ctx:
                    s["samples_context"] = ctx
                    upsert_study_detail_cache(sid, None, None, samples_context=ctx)

    header         = (
        "You have access to the following saved Qiita studies in this project. "
        "When referencing specific studies, ONLY use these IDs and titles:\n"
    )
    detailed_blocks = [_study_detail_block(s, include_samples_context=True) for s in studies]
    full_context    = header + "\n".join(detailed_blocks)
    if len(full_context) <= budget:
        return full_context

    detail_budget = max(1000, budget - len(header) - 400)
    kept_details  = []
    overflow      = []
    running       = 0
    for idx, block in enumerate(detailed_blocks):
        if running + len(block) <= int(detail_budget * 0.65):
            kept_details.append(block)
            running += len(block)
        else:
            overflow.append((studies[idx], block))

    summary_lines = []
    for study, _block in overflow:
        summary = (study.get("summary_text") or "").strip()
        if not summary:
            summary = _truncate(study.get("study_abstract") or "Not available", 240)
        summary_lines.append(
            f"- ID {study.get('study_id')}: {_truncate(study.get('study_title') or 'Untitled study', 130)}\n"
            f"  Summary: {_truncate(summary, 480)}"
        )

    candidate_parts = [header]
    if kept_details:
        candidate_parts.append("Detailed studies:\n" + "\n".join(kept_details))
    if summary_lines:
        candidate_parts.append("Summaries for remaining studies:\n" + "\n".join(summary_lines))
    candidate = "\n\n".join(candidate_parts)
    return candidate[:budget]


def _build_api_messages(messages, study_context_text: str, system_prompt: str):
    prompt = system_prompt
    if study_context_text:
        context_block = f"\n\nSTUDY CONTEXT:\n{study_context_text}"
    else:
        context_block = (
            "\n\nSTUDY CONTEXT:\n"
            "No study records were provided for this request. Do not list specific studies."
        )
    system_content = prompt + context_block
    return [{"role": "system", "content": system_content}] + _normalize_messages(messages)


def llm_chat(messages, study_context_text: str, system_prompt: str, model: str = None):
    resolved = _resolve_model(model)
    llm_client, provider = get_client(resolved)
    api_msgs = _build_api_messages(messages, study_context_text, system_prompt)
    if provider == "anthropic":
        system, msgs = _extract_system_and_messages(api_msgs)
        resp = llm_client.messages.create(model=resolved, max_tokens=4096, system=system, messages=msgs)
        return (resp.content[0].text or "").strip()
    r = llm_client.chat.completions.create(model=resolved, messages=api_msgs)
    return (r.choices[0].message.content or "").strip()


def llm_chat_stream(messages, study_context_text: str, system_prompt: str, model: str = None):
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
