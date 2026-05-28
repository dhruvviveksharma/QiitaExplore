"""LLM context builders, SSE formatter, and streaming wrappers."""

import json
import re

from config import (
    client,
    CHAT_SYSTEM_PROMPT,
    PROJECT_CONTEXT_MAX_CHARS,
    GLOBAL_CONTEXT_MAX_CHARS,
    DEFAULT_MODEL,
    ALLOWED_MODELS,
)


def _resolve_model(model):
    if model and model in ALLOWED_MODELS:
        return model
    return DEFAULT_MODEL


def friendly_llm_error(exc, model=None):
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
from store import (
    get_project_context_summary,
    get_study_detail_cache,
    upsert_study_detail_cache,
)


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
    abstract = _truncate((study.get("study_abstract") or "").strip() or "Not available", 240)
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
    """Fit as many compact study blocks as possible under max_chars."""
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
    out = header_line.strip() + "\n\n" + "\n\n".join(chosen) if chosen else header_line.strip() + "\n"
    return out + "\n"


def _build_project_study_context(project: dict, user_id: str = "default"):
    if not project:
        return None
    studies    = project.get("studies") or []
    if not studies:
        return None
    project_id = project.get("project_id")

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
    if len(full_context) <= PROJECT_CONTEXT_MAX_CHARS:
        return full_context

    budget      = max(1000, PROJECT_CONTEXT_MAX_CHARS - len(header) - 400)
    kept_details = []
    overflow     = []
    running      = 0
    for idx, block in enumerate(detailed_blocks):
        if running + len(block) <= int(budget * 0.65):
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
    if len(candidate) <= PROJECT_CONTEXT_MAX_CHARS:
        return candidate

    project_summary    = None
    if project_id:
        cached             = get_project_context_summary(project_id, user_id)
        source_updated_at  = project.get("updated_at")
        if cached and cached.get("summary_text") and cached.get("source_updated_at") == source_updated_at:
            project_summary = cached.get("summary_text")

    ids_line = ", ".join(str(s.get("study_id")) for s in studies[:60])
    fallback = (
        header
        + f"Study IDs in this project: {ids_line}\n\n"
        + "Project summary:\n"
        + (project_summary or "No cached summary available.")
    )
    return fallback[:PROJECT_CONTEXT_MAX_CHARS]


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


def _build_global_search_context(studies, user_query: str):
    """Build LLM context from auto-searched studies for global chat (compact rows)."""
    if not studies:
        return f'A database search for "{user_query}" returned no matching studies in Qiita. Suggest rephrasing or broadening the query.'
    header = (
        f'The following {len(studies)} studies were retrieved from Qiita based on the query "{user_query}":'
    )
    return _format_discovery_study_list(studies, header, GLOBAL_CONTEXT_MAX_CHARS)


def merge_global_chat_context(selected_studies, db_studies, user_query: str) -> str:
    """
    Combine user-selected browse chips with database search hits for global chat.
    Dedupes DB rows that are already in selected_studies by study_id.
    """
    selected = selected_studies or []
    sel_ids  = {s.get("study_id") for s in selected if s.get("study_id") is not None}
    db_only  = [s for s in (db_studies or []) if s.get("study_id") not in sel_ids]

    intro = (
        "Context layout: (1) USER-SELECTED BROWSE CONTEXT — studies the user attached as chips. "
        "(2) DATABASE SEARCH RESULTS — studies returned by running their latest message against Qiita. "
        "Use database results for breadth and discovery; use selected studies when the question is "
        "specifically about those IDs or for comparison.\n"
    )
    sel_budget = min(14000, max(1500, 400 * max(1, len(selected))))
    db_budget  = max(2000, GLOBAL_CONTEXT_MAX_CHARS - len(intro) - sel_budget - 80)

    sel_header = f"USER-SELECTED BROWSE CONTEXT ({len(selected)} studies):"
    sel_text   = _format_discovery_study_list(selected, sel_header, sel_budget)

    if not db_studies:
        db_text = (
            "DATABASE SEARCH RESULTS:\n"
            "(No matching public studies for this query.)\n"
        )
    elif not db_only:
        db_text = (
            "DATABASE SEARCH RESULTS:\n"
            f"({len(db_studies)} studies matched the query; all are already listed in user-selected context above.)\n"
        )
    else:
        db_header = f'DATABASE SEARCH RESULTS ({len(db_only)} studies) for "{user_query}":'
        db_text   = _format_discovery_study_list(db_only, db_header, db_budget)

    return intro.strip() + "\n\n" + sel_text.strip() + "\n\n" + db_text.strip()


_QUERY_PLAN_SYSTEM = (
    "You are a database query planner for a microbiome study repository.\n"
    "Given the conversation history, output ONLY a JSON object with:\n"
    '  "keywords": list of 20–50 search terms (see expansion rules below)\n'
    '  "description": short human-readable label (e.g. "American Gut Project studies")\n'
    '  "skip_search": true only if the turn asks to filter/sort/analyze studies ALREADY listed'
    " in the conversation — set false for any new discovery request\n"
    '  "page": 0-based page number for pagination. Default 0. Increment by 1 each time the user'
    " explicitly asks for more results from the SAME search (e.g. 'show me more', 'next batch',"
    " 'none of these are right, try more'). Reset to 0 for any new/different search topic.\n\n"
    "KEYWORD EXPANSION RULES — generate every plausible variant:\n"
    "1. ORIGINAL: include the term exactly as the user typed it (unless it is a clear typo)\n"
    "2. SUB-PHRASES: for multi-word inputs, include every subset combination.\n"
    "   Example — 'American Gut Project': include 'American Gut Project', 'American Gut',\n"
    "   'Gut Project', 'American Project', 'American', 'Gut', 'Project'\n"
    "3. ABBREVIATION EXPANSION: if the input is an acronym, expand it in full AND include\n"
    "   every sub-phrase of the expansion. Also keep the acronym itself.\n"
    "   Example — 'AGP': include 'AGP', 'American Gut Project', 'American Gut', 'Gut Project',\n"
    "   'American', 'Gut', 'Project'\n"
    "4. FULL-NAME TO ABBREVIATION: if the input is a full phrase with a well-known acronym,\n"
    "   include the acronym too. Example — 'Inflammatory Bowel Disease': also include 'IBD'\n"
    "5. SYNONYMS: include scientific synonyms, common alternative spellings, related terms,\n"
    "   and Latin/formal names that might appear in study titles or abstracts.\n"
    "   Example — 'wild mice': include 'wild mice', 'wild mouse', 'feral mice', 'feral mouse',\n"
    "   'wild-type', 'wildtype', 'WT mouse', 'WT mice', 'wild-caught', 'free-living mice'\n"
    "   Example — 'shotgun metagenomic' or 'WGS': include 'WGS', 'whole genome shotgun',\n"
    "   'whole metagenome shotgun', 'shotgun metagenomics', 'shotgun sequencing', 'metagenomics',\n"
    "   'metagenomic', 'metagenome', 'whole genome sequencing', 'deep sequencing'\n"
    "6. PLURAL/SINGULAR: include both forms when applicable\n"
    "7. HYPHEN VARIANTS: include both hyphenated and unhyphenated forms\n"
    "   Example — include both 'wild-type' and 'wildtype'\n\n"
    "All keywords are OR'd against study title, abstract, and alias — more terms = wider net.\n"
    "Aim for 30–50 terms. Do not truncate or cap the list early.\n\n"
    "Output valid JSON only. No explanation, no markdown fences."
)


def llm_plan_query(messages: list) -> dict:
    """Call LLM with full conversation history to produce a structured search plan."""
    try:
        r = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "system", "content": _QUERY_PLAN_SYSTEM}] + _normalize_messages(messages),
            timeout=45.0,
        )
        raw = (r.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        plan = json.loads(raw)
        plan.setdefault("keywords", [])
        plan.setdefault("match_mode", "AND")
        plan.setdefault("description", "studies")
        plan.setdefault("page", 0)
        return plan
    except Exception:
        return {"keywords": [], "match_mode": "AND", "description": "studies", "page": 0}


def llm_chat(messages, study_context_text: str, system_prompt: str = None, model: str = None):
    r = client.chat.completions.create(
        model=_resolve_model(model),
        messages=_build_api_messages(messages, study_context_text, system_prompt),
    )
    return (r.choices[0].message.content or "").strip()


def llm_chat_stream(messages, study_context_text: str, system_prompt: str = None, model: str = None):
    stream  = client.chat.completions.create(
        model=_resolve_model(model),
        messages=_build_api_messages(messages, study_context_text, system_prompt),
        stream=True,
    )
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
