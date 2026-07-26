"""Hard project-scope enforcement for the pi-backed agentic project chat.

Today's project chat scopes studies with a prompt sentence only
(llm_helpers.py:_build_project_study_context). This module replaces that with
a real boundary: search is ranked over the workspace's own studies (never the
whole Qiita database), and get_study_report/pin_study refuse any study_id that
isn't a project member before execute_tool() ever runs.

Deliberately reuses the existing tool-result shapes and helpers
(ToolResult, _collect_terms, _empty_input_result, _result_studies from
agent_tools.py; expand_keyword_variants from study_service.py;
_format_discovery_study_list from llm_helpers.py) rather than reimplementing
them, so a project-scoped result renders identically to a global one on the
frontend.
"""

import logging

from services.study_service import expand_keyword_variants
from helpers.llm_helpers import _format_discovery_study_list
from helpers.agent_tools import (
    ToolResult,
    _collect_terms,
    _empty_input_result,
    _result_studies,
)

logger = logging.getLogger(__name__)


def project_member_study_ids(proj: dict) -> set:
    return {
        int(s["study_id"])
        for s in ((proj or {}).get("studies") or [])
        if s.get("study_id") is not None
    }


def _matches_data_types(study: dict, wanted: set) -> bool:
    have = {d.strip().lower() for d in (study.get("data_types") or "").split(",") if d.strip()}
    return bool(wanted & have)


def _filter_by_data_types(studies: list, data_types) -> list:
    """No-op when no data types were requested. Both search entry points narrow
    the workspace this way, so the lowercasing lives here rather than twice."""
    types = [t.strip() for t in (data_types or []) if t]
    if not types:
        return studies
    wanted = {t.lower() for t in types}
    return [s for s in studies if _matches_data_types(s, wanted)]


def _local_relevance(study: dict, kws: list) -> int:
    """Mirror services.study_service.build_relevance_score's weights
    (title=3, pi_name=2, abstract=1) over an already-fetched local row. No
    study_alias field is stored on project_studies, so that weight is dropped."""
    title = (study.get("study_title") or "").lower()
    pi = (study.get("pi_name") or "").lower()
    abstract = (study.get("study_abstract") or "").lower()
    return sum(
        (3 if k in title else 0) + (2 if k in pi else 0) + (1 if k in abstract else 0)
        for k in kws
    )


def project_scoped_search_studies(args: dict, studies: list) -> ToolResult:
    raw_kws, _detect_kws = _collect_terms(args)
    limit = max(1, min(20, int(args.get("limit") or 10)))
    explicit_types = [t.strip() for t in (args.get("data_types") or []) if t]

    if not raw_kws:
        return _empty_input_result(
            "search_studies", "No keywords provided — cannot search.",
            "Search studies", "no keywords", args={"keywords": []},
        )

    candidates = _filter_by_data_types(studies, explicit_types)

    kws = [k.lower() for k in expand_keyword_variants(raw_kws) if k]
    scored = [(s, _local_relevance(s, kws)) for s in candidates]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    ranked = [s for s, score in scored if score > 0][:limit]
    # A small, already-curated workspace with no keyword hit is still more
    # useful shown than empty — fall back to the (still scoped, still
    # data-type-filtered) candidate set rather than reporting no matches.
    if not ranked and candidates:
        ranked = candidates[:limit]

    if not ranked:
        text = "No studies in this project's workspace match those keywords."
    else:
        header = f"search_studies (workspace-scoped) returned {len(ranked)} studies:"
        text = _format_discovery_study_list(ranked, header, 24_000)

    return ToolResult(
        text=text,
        label="Searched workspace studies",
        detail=f"{len(ranked)} of {len(studies)} workspace studies",
        ui_payload={
            "kind": "tool_call",
            "tool": "search_studies",
            "args": {"keywords": raw_kws, "data_types": explicit_types or None, "limit": limit},
            "result_summary": f"{len(ranked)} studies" if ranked else "no matches",
            "result_studies": _result_studies(ranked, via="workspace"),
        },
    )


def project_scoped_search_by_sample(args: dict, studies: list) -> ToolResult:
    from helpers.sample_search import _probe_fields_raw, _parallel_probe, _hydrate_headers

    field_filters = [
        f for f in (args.get("field_filters") or [])
        if isinstance(f, dict) and f.get("field") and f.get("value")
    ]
    keywords = [str(k).strip() for k in (args.get("keywords") or []) if str(k).strip()]
    data_types = [str(t).strip() for t in (args.get("data_types") or []) if t]
    limit = max(1, min(20, int(args.get("limit") or 8)))

    if not field_filters and not keywords:
        return _empty_input_result(
            "search_by_sample",
            "No field filters or keywords provided — cannot search sample metadata.",
            "Sample metadata search", "no criteria",
        )

    candidates = _filter_by_data_types(studies, data_types)
    member_ids = [int(s["study_id"]) for s in candidates if s.get("study_id") is not None]

    if not member_ids:
        return ToolResult(
            text="This project's workspace has no studies matching those data types to search.",
            label="Sample metadata search", detail="empty workspace",
        )

    matched_ids = _parallel_probe(
        member_ids,
        lambda pool, sid: _probe_fields_raw(pool, sid, field_filters, keywords),
        "project_field_filter_search",
        pool_size=min(16, len(member_ids)),
    )
    results = _hydrate_headers(matched_ids)[:limit]

    if not results:
        text = "No studies in this project's workspace have samples matching those metadata criteria."
    else:
        header = f"search_by_sample (workspace-scoped) returned {len(results)} studies with matching samples:"
        text = _format_discovery_study_list(results, header, 24_000)

    return ToolResult(
        text=text,
        label="Searched workspace sample metadata",
        detail=f"{len(results)} of {len(member_ids)} workspace studies",
        ui_payload={
            "kind": "tool_call",
            "tool": "search_by_sample",
            "args": {"field_filters": field_filters, "keywords": keywords, "data_types": data_types},
            "result_summary": f"{len(results)} studies" if results else "no matches",
            "result_studies": _result_studies(results, via="workspace"),
        },
    )


def enforce_project_get_report(args: dict, member_ids: set):
    """Return a refusal ToolResult if study_id isn't a project member OR isn't
    parseable, else None to signal the caller should proceed via execute_tool().

    Must fail CLOSED on a malformed id. Returning None here used to mean
    "proceed" with args unchanged — args["study_id"] then reached
    _tool_get_study_report (agent_tools.py), whose `int(args.get("study_id")
    or 0)` has no guard of its own and raises uncaught, 500ing the request
    instead of refusing cleanly."""
    try:
        sid = int(args.get("study_id") or 0)
    except (TypeError, ValueError):
        return ToolResult(
            text="That study_id is not valid — could not parse it as a study number.",
            label="Invalid study_id", detail="unparseable",
        )
    if sid in member_ids:
        return None
    return ToolResult(
        text=(
            f"Study {sid} is not in this project's workspace. This chat can only "
            "reference studies that have been added to the project — add it from "
            "the study browser first, or ask in a global chat instead."
        ),
        label=f"Study {sid} not in workspace",
        detail="out of scope",
    )


def enforce_project_pin(args: dict, member_ids: set):
    """Filter study_ids to project members in place. Returns (refusal, dropped):
    `refusal` is a ToolResult if nothing survives, else None — the caller
    proceeds via execute_tool() with args['study_ids'] already trimmed to
    in-scope ids. `dropped` is the list of out-of-scope ids that were removed,
    for the caller to fold into the final response text.

    dropped is a plain return value, not smuggled through args as a
    "_dropped_out_of_scope" key the way an earlier version of this function
    did — args is the dict that goes on to become execute_tool()'s call
    arguments, so anything stashed there has to be remembered and popped
    again before that call, and a forgotten pop leaks an internal bookkeeping
    key into the tool's own args/ui_payload.

    Parses each element independently (mirroring _tool_pin_study's own
    per-element tolerance in agent_tools.py) rather than aborting the whole
    list on the first unparseable entry. That is a security boundary, not a
    style choice: an all-or-nothing `[int(x) for x in raw_ids]` raises on the
    first bad element, before any scope filtering runs — a request like
    study_ids=["x", <non-member-id>] would then skip scope enforcement
    entirely, reaching execute_tool() with the non-member id intact, and
    _tool_pin_study's own per-element skip would pin it from a project chat,
    past the workspace boundary this module exists to enforce."""
    raw_ids = args.get("study_ids") or []
    ids = []
    for x in raw_ids:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        return ToolResult(
            text="No valid study IDs provided to pin.",
            label="Pin refused", detail="no valid ids",
        ), []
    in_scope = [i for i in ids if i in member_ids]
    out_of_scope = [i for i in ids if i not in member_ids]
    if out_of_scope and not in_scope:
        return ToolResult(
            text=(
                f"None of {ids} are in this project's workspace — nothing pinned. "
                "This chat can only pin studies already added to the project."
            ),
            label="Pin refused", detail="out of scope",
        ), []
    args["study_ids"] = in_scope
    return None, out_of_scope
    return None
