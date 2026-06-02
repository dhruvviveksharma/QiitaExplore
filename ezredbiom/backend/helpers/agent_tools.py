"""Tool registry and execution dispatch for the agentic chat loop."""

from dataclasses import dataclass
from typing import Optional

from services.study_service import build_where_from_plan, search_studies_with_sql
from helpers.llm_helpers import _format_discovery_study_list
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
                "Results are ranked by relevance (best matches first). "
                "Include ALL relevant terms from the full conversation so refinements accumulate "
                "(e.g. if the user asked for 'mouse gut' then 'shotgun', search "
                "['mouse','gut','shotgun','metagenomic']). Returns the top matching studies "
                "with title, PI, sample count, data types, and abstract."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of search terms (synonyms, acronyms, plural forms). More is better.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max studies to return (1–20, default 8 — show the user the best handful).",
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


def execute_tool(name: str, args: dict, *, scope: str, chat_id: str) -> ToolResult:
    """Dispatch a tool call by name and return a ToolResult."""
    if name == "search_studies":
        return _tool_search_studies(args)
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


def _tool_search_studies(args: dict) -> ToolResult:
    keywords = [str(k).strip() for k in (args.get("keywords") or []) if str(k).strip()]
    limit    = max(1, min(20, int(args.get("limit") or 8)))
    if not keywords:
        return ToolResult(
            text="No keywords provided — cannot search.",
            label="Search studies", detail="no keywords",
            ui_payload={"kind": "tool_call", "tool": "search_studies", "args": {"keywords": []}, "result_summary": "no keywords"},
        )
    where, params = build_where_from_plan({"keywords": keywords})
    # Rank by keyword relevance so the top `limit` rows are the best matches,
    # not the lowest study IDs.
    studies = search_studies_with_sql(where, params, limit=limit, relevance_keywords=keywords)
    if not studies:
        text = "No matching public studies found for those keywords."
    else:
        header = f"search_studies returned the top {len(studies)} studies by relevance:"
        text   = _format_discovery_study_list(studies, header, 8_000)
    return ToolResult(
        text=text,
        label="Searched Qiita database",
        detail=f"top {len(studies)} of matches",
        ui_payload={
            "kind": "tool_call",
            "tool": "search_studies",
            "args": {"keywords": keywords, "limit": limit},
            "result_summary": f"{len(studies)} studies returned" if studies else "no matches",
            "result_studies": [
                {
                    "study_id":    s.get("study_id"),
                    "study_title": s.get("study_title"),
                    "pi_name":     s.get("pi_name"),
                    "num_samples": s.get("num_samples"),
                    "data_types":  s.get("data_types"),
                }
                for s in studies
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
