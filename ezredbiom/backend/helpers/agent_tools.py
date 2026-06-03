"""Tool registry and execution dispatch for the agentic chat loop."""

from dataclasses import dataclass
from typing import Optional

from services.study_service import (
    build_where_from_plan, search_studies_with_sql,
    detect_data_types, expand_keyword_variants,
)
from helpers.llm_helpers import _format_discovery_study_list
from helpers.sample_search import search_studies_by_sample_meta
from helpers.qiita_fetch import (
    _build_samples_report_payload,
    _build_full_samples_block,
    _pin_studies_validated,
)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_studies",
            "description": (
                "Search the Qiita public microbiome database for studies matching keywords. "
                "Results are ranked by relevance and include host-metadata matches. "
                "Issue EXACTLY ONE call with ALL keywords — never multiple parallel calls with "
                "different filters (that yields redundant 0-result searches). "
                "Include ALL relevant terms from the full conversation so refinements accumulate "
                "(e.g. if the user asked for 'mouse gut' then 'shotgun', search "
                "['mouse','mice','gut','shotgun','metagenomic']). Include BOTH singular and "
                "plural forms and known synonyms/strains (e.g. mouse, mice, murine, Mus musculus, C57BL/6). "
                "Only set data_types/investigation_types when the user EXPLICITLY names a sequencing "
                "type; for a plain topic query, OMIT them (they AND-filter and usually return 0)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Topic search terms including plurals, synonyms, strains. More is better.",
                    },
                    "data_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Filter to studies with these Qiita data types (AND filter — narrows results). "
                            "Valid values: '16S', '18S', 'ITS', 'Metagenomic', 'Metatranscriptomic', "
                            "'Metabolomic', 'Proteomic', 'Multiomic', 'Genome Isolate', 'Full Length Operon'. "
                            "Use 'Metagenomic' for shotgun/WGS/metagenome (broad, ~605 studies). "
                            "Omit to search all data types."
                        ),
                    },
                    "investigation_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional finer filter within a data_type (e.g. 'WGS', 'shotgun_metagenomics'). "
                            "Only set when the user is THIS specific — 'WGS' filters from ~605 to ~521 studies; "
                            "'shotgun_metagenomics' narrows to only 18. Default: omit."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max studies to return (1–20, default 8).",
                    },
                },
                "required": ["keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_study_report",
            "description": (
                "Load full sample-level metadata for a specific Qiita study. "
                "Shows all samples with their metadata fields. "
                "Also pins the study to this chat so it stays in context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "study_id": {
                        "type": "integer",
                        "description": "The Qiita study ID to fetch.",
                    },
                },
                "required": ["study_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pin_study",
            "description": (
                "Attach one or more studies to this chat for persistent deep context. "
                "Pinned studies are loaded in full on each message. Cap: 10 studies."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "study_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of Qiita study IDs to pin.",
                    },
                },
                "required": ["study_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_diversity",
            "description": (
                "Compute alpha/beta diversity metrics from study OTU tables. "
                "Currently unavailable — BIOM ingestion is pending (TKT-010)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "study_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Study IDs to compute diversity for.",
                    },
                    "metric": {
                        "type": "string",
                        "description": "Diversity metric (e.g. 'shannon', 'bray_curtis').",
                    },
                },
                "required": ["study_ids"],
            },
        },
    },
]


@dataclass
class ToolResult:
    text: str                          # Fed back to the model as a tool message
    label: str                         # Shown in step_done UI
    detail: str = ""                   # Shown as sub-label in step_done UI
    ui_payload: Optional[dict] = None  # If set, emitted as a `ui` SSE event


def execute_tool(name: str, args: dict, *, scope: str, chat_id: str,
                 deep_search: bool = False) -> ToolResult:
    """Dispatch a tool call by name and return a ToolResult."""
    if name == "search_studies":
        return _tool_search_studies(args, deep_search=deep_search)
    if name == "get_study_report":
        return _tool_get_study_report(args, scope=scope, chat_id=chat_id)
    if name == "pin_study":
        return _tool_pin_study(args, scope=scope, chat_id=chat_id)
    if name == "compute_diversity":
        return _tool_compute_diversity(args)
    return ToolResult(
        text=f"Unknown tool: {name}",
        label=f"Tool error ({name})",
        detail="unknown tool",
    )


def _tool_search_studies(args: dict, *, deep_search: bool = False) -> ToolResult:
    raw_kws         = [str(k).strip() for k in (args.get("keywords") or []) if str(k).strip()]
    limit           = max(1, min(20, int(args.get("limit") or 8)))
    explicit_types  = [t.strip() for t in (args.get("data_types") or []) if t]
    explicit_inv    = [t.strip() for t in (args.get("investigation_types") or []) if t]

    if not raw_kws:
        return ToolResult(
            text="No keywords provided — cannot search.",
            label="Search studies", detail="no keywords",
            ui_payload={"kind": "tool_call", "tool": "search_studies",
                        "args": {"keywords": []}, "result_summary": "no keywords"},
        )

    # Phase 2: expand morphological variants (mouse → also mice)
    kws = expand_keyword_variants(raw_kws)

    # Phase 1: merge explicit + auto-detected data types from keywords
    auto_types     = detect_data_types(raw_kws)
    effective_types = list(dict.fromkeys(explicit_types + auto_types)) or None
    effective_inv  = explicit_inv or None

    # Text search (topic OR + data-type AND)
    where, params = build_where_from_plan({"keywords": kws})
    text_studies = search_studies_with_sql(
        where, params,
        limit=limit * 2,             # over-fetch to leave room for sample hits
        relevance_keywords=kws,
        data_types=effective_types,
        investigation_types=effective_inv,
    )
    text_ids = {s["study_id"] for s in text_studies}

    # Phase 3: sample-metadata search — only in /deepsearch mode
    if deep_search:
        sample_studies = search_studies_by_sample_meta(
            raw_kws,
            data_types=effective_types,
            exclude_ids=text_ids,
            max_candidates=500,
            pool_size=16,
        )
    else:
        sample_studies = []

    # Merge: text hits first (win dedup); sample hits fill gaps; re-rank; trim
    seen, merged = {}, []
    for s in text_studies + sample_studies:
        sid = s["study_id"]
        if sid not in seen:
            seen[sid] = s
            merged.append(s)

    # Re-rank merged list by same keyword relevance heuristic
    def _score(s):
        title    = (s.get("study_title") or "").lower()
        abstract = (s.get("study_abstract") or "").lower()
        return sum(3 if k.lower() in title else (1 if k.lower() in abstract else 0)
                   for k in kws)
    merged.sort(key=_score, reverse=True)
    merged = merged[:limit]

    if not merged:
        text = "No matching public studies found for those keywords."
    else:
        header = f"search_studies returned the top {len(merged)} studies:"
        text   = _format_discovery_study_list(merged, header, 8_000)

    label = "Deep-searched Qiita database" if deep_search else "Searched Qiita database"
    detail_suffix = f" (incl. {len(sample_studies)} from sample metadata of ≤500 studies)" if sample_studies else (" (sample scan: 0 matches)" if deep_search else "")
    return ToolResult(
        text=text,
        label=label,
        detail=f"top {len(merged)} results{detail_suffix}",
        ui_payload={
            "kind":           "tool_call",
            "tool":           "search_studies",
            "args":           {"keywords": raw_kws, "data_types": effective_types, "limit": limit},
            "result_summary": f"{len(merged)} studies" if merged else "no matches",
            "result_studies": [
                {
                    "study_id":    s.get("study_id"),
                    "study_title": s.get("study_title"),
                    "pi_name":     s.get("pi_name"),
                    "num_samples": s.get("num_samples"),
                    "data_types":  s.get("data_types"),
                    "via":         s.get("via", "text"),
                }
                for s in merged
            ],
        },
    )


def _tool_get_study_report(args: dict, *, scope: str, chat_id: str) -> ToolResult:
    study_id = int(args.get("study_id") or 0)
    try:
        ui_payload  = _build_samples_report_payload(study_id)
        num_samples = (ui_payload.get("header") or {}).get("num_samples") or len(ui_payload.get("samples") or [])
        text_block  = _build_full_samples_block(study_id, budget_chars=4_000)
        # Pin the study so it stays in deep context (mirrors existing /report behavior)
        try:
            _pin_studies_validated(chat_id, scope, [study_id])
        except Exception:
            pass
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


def _tool_compute_diversity(_args: dict) -> ToolResult:
    return ToolResult(
        text=(
            "Diversity analysis is not yet available. "
            "BIOM/OTU ingestion is pending (TKT-010). "
            "Once implemented, this tool will compute Shannon, Faith's PD, "
            "Bray-Curtis, and UniFrac metrics from study OTU tables."
        ),
        label="Diversity (unavailable)",
        detail="pending TKT-010",
    )
