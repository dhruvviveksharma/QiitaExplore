"""Tool registry and execution dispatch for the agentic chat loop."""

import logging
from dataclasses import dataclass
from typing import Optional

from services.study_service import (
    build_where_from_plan, search_studies_with_sql,
    detect_data_types, expand_keyword_variants,
)
from services.relevance import (
    normalize_entities, prepare_pi_filter, build_pi_required_filter,
    finalize_search_results, pi_detail_suffix,
)
from helpers.llm_helpers import _format_discovery_study_list
from helpers.sample_search import search_studies_by_sample_meta, search_studies_by_field_filters
from helpers.qiita_fetch import _build_samples_report_payload, _pin_studies_validated
from helpers.pinned_context import _build_full_samples_block
from config import (SAMPLE_SEARCH_DEFAULT_CANDIDATES, SAMPLE_SEARCH_DEEP_CANDIDATES,
                    PINNED_CHARS_PER_STUDY)
from store import SCOPE_PROJECT, SCOPE_GLOBAL, get_project_id_for_chat, get_project_studies_only

logger = logging.getLogger(__name__)

from helpers.agent_tool_schemas import TOOL_SCHEMAS  # re-exported for agent.py

@dataclass
class ToolResult:
    text: str                          # Fed back to the model as a tool message
    label: str                         # Shown in step_done UI
    detail: str = ""                   # Shown as sub-label in step_done UI
    ui_payload: Optional[dict] = None  # If set, emitted as a `ui` SSE event


def _result_studies(studies, via=None):
    """Flatten study dicts into the {study_id, study_title, pi_name, num_samples,
    data_types, via} shape used in tool-call ui_payloads. `via` overrides the
    per-study origin tag when the caller already knows it (e.g. all results came
    from the sample-metadata search); otherwise falls back to each study's own."""
    return [
        {
            "study_id":    s.get("study_id"),
            "study_title": s.get("study_title"),
            "pi_name":     s.get("pi_name"),
            "num_samples": s.get("num_samples"),
            "data_types":  s.get("data_types"),
            "via":         via or s.get("via", "text"),
        }
        for s in studies
    ]


def _empty_input_result(tool, text, label, detail, args=None):
    """Shared shape for the 'no search criteria provided' early-return ToolResult."""
    return ToolResult(text=text, label=label, detail=detail, ui_payload={
        "kind": "tool_call", "tool": tool, "args": args or {}, "result_summary": detail,
    })


def _collect_terms(args: dict) -> tuple:
    """Pool dimension slots and entity texts into (raw_kws, detect_kws)."""
    def _clean(lst):
        return [str(k).strip() for k in (lst or []) if str(k).strip()]

    entities = normalize_entities(args)
    entity_texts = [e["text"] for e in entities]

    seen, raw_kws = set(), []
    for slot in ("organism", "qualifier", "body_site",
                 "condition_or_intervention", "project_or_pi", "keywords"):
        for t in _clean(args.get(slot) or []):
            if t not in seen:
                seen.add(t)
                raw_kws.append(t)
    for t in entity_texts:
        if t not in seen:
            seen.add(t)
            raw_kws.append(t)

    detect_kws = _clean(args.get("keywords") or [])
    return raw_kws, detect_kws


def _allowed_project_study_ids(project_id: str) -> set:
    proj = get_project_studies_only(project_id) or {}
    return {
        int(s["study_id"])
        for s in (proj.get("studies") or [])
        if s.get("study_id") is not None
    }


def execute_tool(name: str, args: dict, *, scope: str, chat_id: str,
                 deep_search: bool = False) -> ToolResult:
    """Dispatch a tool call by name and return a ToolResult."""
    if scope == SCOPE_GLOBAL:
        return _execute_global_tool(name, args, scope=scope, chat_id=chat_id, deep_search=deep_search)
    if scope == SCOPE_PROJECT:
        return _execute_project_tool(name, args, chat_id=chat_id)
    return ToolResult(
        text=f"Unknown scope: {scope}",
        label=f"Tool error ({name})",
        detail="unknown scope",
    )


def _execute_global_tool(name: str, args: dict, *, scope: str, chat_id: str,
                         deep_search: bool = False) -> ToolResult:
    if name == "search_studies":
        return _tool_search_studies(args, deep_search=deep_search)
    if name == "get_study_report":
        return _tool_get_study_report(args, scope=scope, chat_id=chat_id)
    if name == "pin_study":
        return _tool_pin_study(args, scope=scope, chat_id=chat_id)
    if name == "search_by_sample":
        return _tool_search_by_sample(args)
    return ToolResult(
        text=f"Unknown tool: {name}",
        label=f"Tool error ({name})",
        detail="unknown tool",
    )


def _execute_project_tool(name: str, args: dict, *, chat_id: str) -> ToolResult:
    if name == "pin_study":
        return _tool_pin_study(args, scope=SCOPE_PROJECT, chat_id=chat_id)
    project_id = get_project_id_for_chat(chat_id)
    if not project_id:
        return ToolResult(
            text="This chat is not attached to a project.",
            label="Tool error",
            detail="no project for chat",
        )
    if name == "search_project_studies":
        return _tool_search_project_studies(args, project_id=project_id)
    if name == "get_project_study_report":
        return _tool_get_project_study_report(args, project_id=project_id)
    return ToolResult(
        text=f"Tool {name} is not available in project chat.",
        label=f"Tool error ({name})",
        detail="not permitted in project scope",
    )


def _score_project_study(study: dict, keywords: list) -> int:
    if not keywords:
        return 1
    hay = " ".join(
        str(study.get(k) or "")
        for k in ("study_title", "study_abstract", "pi_name", "data_types", "summary_text")
    ).lower()
    return sum(1 for kw in keywords if kw.lower() in hay)


def _tool_search_project_studies(args: dict, *, project_id: str) -> ToolResult:
    proj = get_project_studies_only(project_id) or {}
    studies = list(proj.get("studies") or [])
    raw_kws = [str(k).strip() for k in (args.get("keywords") or []) if str(k).strip()]
    limit = max(1, min(20, int(args.get("limit") or 10)))

    scored = [(s, _score_project_study(s, raw_kws)) for s in studies]
    if raw_kws:
        scored = [(s, sc) for s, sc in scored if sc > 0]
    scored.sort(key=lambda x: (-x[1], int(x[0].get("study_id") or 0)))
    merged = [s for s, _ in scored[:limit]]

    if not merged:
        text = "No matching studies found in this project for those keywords."
    else:
        header = f"search_project_studies returned {len(merged)} studies from this project:"
        text = _format_discovery_study_list(
            merged, header, 24_000, report_tool_name="get_project_study_report")

    return ToolResult(
        text=text,
        label="Searched project studies",
        detail=f"{len(merged)} results" if merged else "no matches",
        ui_payload={
            "kind":           "tool_call",
            "tool":           "search_project_studies",
            "args":           {"keywords": raw_kws, "limit": limit},
            "result_summary": f"{len(merged)} studies" if merged else "no matches",
            "result_studies": _result_studies(merged, via="project"),
        },
    )


def _tool_get_project_study_report(args: dict, *, project_id: str) -> ToolResult:
    study_id = int(args.get("study_id") or 0)
    allowed = _allowed_project_study_ids(project_id)
    if study_id not in allowed:
        return ToolResult(
            text=f"Study {study_id} is not part of this project.",
            label=f"Study {study_id}",
            detail="not in project",
        )
    try:
        ui_payload  = _build_samples_report_payload(study_id)
        num_samples = (ui_payload.get("header") or {}).get("num_samples") or len(ui_payload.get("samples") or [])
        text_block  = _build_full_samples_block(
            study_id, budget_chars=PINNED_CHARS_PER_STUDY,
            report_tool_name="get_project_study_report")
        return ToolResult(
            text=f"Full sample report for study {study_id} ({num_samples} samples):\n{text_block}",
            label=f"Loaded study {study_id} report",
            detail=f"{num_samples} samples",
            ui_payload=ui_payload,
        )
    except ValueError:
        return ToolResult(
            text=f"Study {study_id} is private or has no accessible data in Qiita.",
            label=f"Study {study_id}",
            detail="private or not found",
        )


def _tool_search_studies(args: dict, *, deep_search: bool = False) -> ToolResult:
    entities = normalize_entities(args)
    _, resolved_pis, veto_applied, applied_pi = prepare_pi_filter(entities)
    pi_sql, pi_params = build_pi_required_filter(resolved_pis) if veto_applied else (None, [])

    raw_kws, detect_kws = _collect_terms(args)
    limit          = max(1, min(20, int(args.get("limit") or 10)))
    explicit_types = [t.strip() for t in (args.get("data_types") or []) if t]
    explicit_inv   = [t.strip() for t in (args.get("investigation_types") or []) if t]

    logger.info(
        "[search_studies] raw_kws=%d detect_kws=%d deep=%s explicit_types=%s limit=%d veto=%s",
        len(raw_kws), len(detect_kws), deep_search, explicit_types or None, limit, veto_applied,
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("[search_studies] raw_kws=%s applied_pi=%s", raw_kws, applied_pi)

    if not raw_kws:
        return _empty_input_result("search_studies", "No keywords provided — cannot search.",
                                    "Search studies", "no keywords", args={"keywords": []})

    kws = expand_keyword_variants(raw_kws)
    logger.info("[search_studies] expanded_kws=%d  %s", len(kws), kws[:10])

    auto_types      = detect_data_types(detect_kws)
    effective_types = list(dict.fromkeys(explicit_types + auto_types)) or None
    effective_inv   = explicit_inv or None

    where, params = build_where_from_plan({"keywords": kws})
    text_studies, sql_str = search_studies_with_sql(
        where, params,
        limit=limit * 2,
        relevance_keywords=kws,
        data_types=effective_types,
        investigation_types=effective_inv,
        pi_filter_sql=pi_sql,
        pi_filter_params=pi_params,
        return_sql=True,
    )
    text_ids = {s["study_id"] for s in text_studies}
    logger.info("[search_studies] text_hits=%d", len(text_studies))

    max_cands = SAMPLE_SEARCH_DEEP_CANDIDATES if deep_search else SAMPLE_SEARCH_DEFAULT_CANDIDATES
    sample_studies = search_studies_by_sample_meta(
        kws,
        data_types=effective_types,
        exclude_ids=text_ids,
        max_candidates=max_cands,
        pool_size=16,
        resolved_pis=resolved_pis if veto_applied else None,
    )
    logger.info("[search_studies] sample_hits=%d (deep=%s max_cands=%d)",
                len(sample_studies), deep_search, max_cands)

    seen_ids, merged = {}, []
    for s in text_studies + sample_studies:
        sid = s["study_id"]
        if sid not in seen_ids:
            seen_ids[sid] = s
            merged.append(s)

    merged = finalize_search_results(
        merged, kws, resolved_pis=resolved_pis, veto_applied=veto_applied, limit=limit,
    )

    logger.info("[search_studies] final_merged=%d (after trim to limit=%d)", len(merged), limit)

    if not merged:
        text = "No matching public studies found for those keywords."
    else:
        header = f"search_studies returned the top {len(merged)} studies:"
        text   = _format_discovery_study_list(merged, header, 24_000)

    label = "Deep-searched Qiita database" if deep_search else "Searched Qiita database"
    detail_suffix = (
        f" (incl. {len(sample_studies)} from sample metadata of ≤{max_cands} studies)"
        if sample_studies else f" (sample scan: 0 matches, ≤{max_cands} studies)"
    )
    pi_suffix = pi_detail_suffix(applied_pi)
    applied_filters = {"pi": applied_pi} if applied_pi.get("input") else {}

    return ToolResult(
        text=text,
        label=label,
        detail=f"top {len(merged)} results{detail_suffix}{pi_suffix}",
        ui_payload={
            "kind":           "tool_call",
            "tool":           "search_studies",
            "args":           {"keywords": raw_kws, "data_types": effective_types, "limit": limit},
            "sql_query":      sql_str,
            "applied_filters": applied_filters,
            "result_summary": f"{len(merged)} studies" if merged else "no matches",
            "result_studies": _result_studies(merged),
        },
    )


def _tool_get_study_report(args: dict, *, scope: str, chat_id: str) -> ToolResult:
    study_id = int(args.get("study_id") or 0)
    try:
        ui_payload  = _build_samples_report_payload(study_id)
        num_samples = (ui_payload.get("header") or {}).get("num_samples") or len(ui_payload.get("samples") or [])
        text_block  = _build_full_samples_block(study_id, budget_chars=PINNED_CHARS_PER_STUDY)
        return ToolResult(
            text=f"Full sample report for study {study_id} ({num_samples} samples):\n{text_block}",
            label=f"Loaded study {study_id} report",
            detail=f"{num_samples} samples",
            ui_payload=ui_payload,
        )
    except ValueError:
        return ToolResult(
            text=f"Study {study_id} is private or has no accessible data in Qiita.",
            label=f"Study {study_id}",
            detail="private or not found",
        )


def _tool_pin_study(args: dict, *, scope: str, chat_id: str) -> ToolResult:
    raw_ids   = args.get("study_ids") or []
    study_ids = []
    for x in raw_ids:
        try:
            study_ids.append(int(x))
        except (TypeError, ValueError):
            pass
    study_ids = study_ids[:10]
    if not study_ids:
        return ToolResult(text="No valid study IDs provided to pin.", label="Pin studies", detail="none")
    pinned_now, invalid, rejected, all_pinned = _pin_studies_validated(chat_id, scope, study_ids)
    parts = []
    if pinned_now:
        parts.append(f"{len(pinned_now)} pinned: {', '.join(str(s) for s in pinned_now)}")
    if invalid:
        parts.append(f"{len(invalid)} not found/private: {', '.join(str(s) for s in invalid)}")
    if rejected:
        parts.append(f"{len(rejected)} rejected (cap reached): {', '.join(str(s) for s in rejected)}")
    summary = ". ".join(parts) or "No studies changed."
    return ToolResult(
        text=f"pin_study result: {summary}. All pinned: {all_pinned}",
        label="Studies pinned",
        detail=f"{len(pinned_now)} pinned · {len(all_pinned)} total",
        ui_payload={
            "kind": "tool_call",
            "tool": "pin_study",
            "args": {"study_ids": study_ids},
            "result_summary": summary,
        },
    )


def _tool_search_by_sample(args: dict) -> ToolResult:
    field_filters = [f for f in (args.get("field_filters") or [])
                     if isinstance(f, dict) and f.get("field") and f.get("value")]
    keywords      = [str(k).strip() for k in (args.get("keywords") or []) if str(k).strip()]
    data_types    = [str(t).strip() for t in (args.get("data_types") or []) if t]
    limit         = max(1, min(20, int(args.get("limit") or 8)))

    logger.info(
        "[search_by_sample] field_filters=%d keywords=%d data_types=%s limit=%d",
        len(field_filters), len(keywords), data_types or None, limit,
    )

    if not field_filters and not keywords:
        return _empty_input_result(
            "search_by_sample", "No field filters or keywords provided — cannot search sample metadata.",
            "Sample metadata search", "no criteria",
        )

    results = search_studies_by_field_filters(
        field_filters=field_filters,
        keywords=keywords,
        data_types=data_types or None,
        max_candidates=200,
        pool_size=16,
    )
    results = results[:limit]

    if not results:
        text = "No studies found with samples matching those metadata criteria."
    else:
        header = f"search_by_sample returned {len(results)} studies with matching samples:"
        text   = _format_discovery_study_list(results, header, 24_000)

    return ToolResult(
        text=text,
        label="Searched sample metadata",
        detail=f"{len(results)} studies" if results else "no matches",
        ui_payload={
            "kind":           "tool_call",
            "tool":           "search_by_sample",
            "args":           {"field_filters": field_filters, "keywords": keywords, "data_types": data_types},
            "result_summary": f"{len(results)} studies" if results else "no matches",
            "result_studies": _result_studies(results, via="sample_metadata"),
        },
    )
