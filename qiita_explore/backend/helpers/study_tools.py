"""Chat tools over one study, available in global and project chats alike:
preps (with data types), samples, one sample's metadata, a prep's processing
graph, artifact files, propose_aggregation_add (a confirm card — it never
writes), and resolve_study (which study does free text mean).

Each tool returns compact text for the model and a small ui_payload (ids only)
that the chat renders as an interactive widget (frontend/js/chat_study_widgets.js),
which fetches its data through the same REST endpoints as the study view.

Rules every tool follows:
  - In a project chat the study must be in the project — checked before any
    Qiita read. Every study must be public.
  - The model sees filenames and file ids, never server paths: tool text goes
    to the LLM provider, and paths stay in the widget.
  - Text is capped at _TEXT_MAX, key facts first (history keeps only 2,000).
"""
import logging
import time
from collections import Counter

from services.llm import browse_query_to_sql
from services.relevance import build_pi_required_filter
from services.study_service import detect_data_types, expand_keyword_variants, search_studies_with_sql
from store import AGGREGATION_STUDIES_CAP, SCOPE_PROJECT, allowed_project_study_ids, \
    get_project_id_for_chat, get_project_studies_only, list_aggregations, list_pinned_studies
from helpers.fastq_manifest import group_matches
from helpers.sample_files import get_sample_files
from helpers.pg_pool import pooled_fetchall
from helpers.qiita_fetch import (
    _PUBLIC_ARTIFACT_EXISTS, _fetch_study_header_cached, _fetch_study_headers, is_study_public,
)
from helpers.study_detail import load_preps_and_graph
from helpers.study_resolve import acronym_rank, identifying_tokens, resolve_text
from helpers.study_samples import (
    artifact_preps, fetch_prep_samples, fetch_sample_fields, list_study_sample_ids, prep_data_types,
    prep_groups, prep_membership, study_columns,
)
from helpers.tool_result import ToolResult

logger = logging.getLogger(__name__)

_TEXT_MAX = 6000
_PREP_ROWS = 40
_GRAPH_NODES = 80
_FILES_PER_ARTIFACT = 15
_FILE_LINES = 120
_VALUE_MAX = 200
_CANDIDATES = 6


def _clip(text, limit=_TEXT_MAX):
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 20)
    return text[: cut if cut > 0 else limit - 20] + "\n… (truncated)"


def _err(name, text, detail):
    return ToolResult(text=text, label=f"{name} failed", detail=detail)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def execute_study_tool(name, args, *, scope, chat_id, user_id=None):
    """Dispatch one of STUDY_TOOL_NAMES (helpers/agent_tool_schemas.py).
    `user_id` is only read by the aggregation tools (the user's aggregations)."""
    args = args or {}
    if name == "resolve_study":
        return _tool_resolve_study(args, scope=scope, chat_id=chat_id)
    if name in ("list_aggregations", "save_chat_aggregation"):
        from helpers import aggregation_tools   # imported here: it reuses this module's helpers
        return aggregation_tools.execute(name, args, scope=scope, chat_id=chat_id, user_id=user_id)
    sid = _int(args.get("study_id"))
    if sid is None:
        return _err(name, "study_id must be an integer Qiita study id.", "bad study_id")
    if scope == SCOPE_PROJECT:
        project_id = get_project_id_for_chat(chat_id)
        if not project_id or sid not in allowed_project_study_ids(project_id):
            return _err(name, f"Study {sid} is not in this project. Only studies saved in the "
                              "project can be shown here; suggest adding it via Browse.", "not in project")
    if not is_study_public(sid):
        return _err(name, f"Study {sid} is private or does not exist.", "private or not found")
    header = _fetch_study_header_cached(sid) or {}
    if name == "propose_aggregation_add":
        return _tool_propose_aggregation(sid, header, args, user_id)
    if name == "add_to_chat_aggregation":
        from helpers.aggregation_tools import tool_add_to_chat_aggregation
        return tool_add_to_chat_aggregation(sid, header, args, scope=scope, chat_id=chat_id, user_id=user_id)
    tool = {
        "get_study_preps":     _tool_preps,
        "show_study_samples":  _tool_samples,
        "get_sample_metadata": _tool_sample_metadata,
        "get_prep_graph":      _tool_graph,
        "list_artifact_files": _tool_files,
    }.get(name)
    if tool is None:
        return _err(name, f"Unknown tool: {name}", "unknown tool")
    return tool(sid, header, args)


# ── shared graph helpers (Python twin of filterGraphByPrep, merge_artifacts.js) ──

def _archived(n):
    return n.get("kind") == "artifact" and n.get("visibility") == "archived"


def node_preps(graph):
    """{node_id: prep_id or None}: the prep of each node's root. The graph is a
    forest, so every node belongs to the prep its root artifact is linked to."""
    by_id = {n["node_id"]: n for n in graph}
    out = {}
    for n in graph:
        path, cur, steps = [], n, 0
        while cur is not None and cur["node_id"] not in out and steps <= len(graph):
            if cur.get("kind") == "artifact" and cur.get("prep_template_id") is not None:
                out[cur["node_id"]] = cur["prep_template_id"]
                break
            path.append(cur["node_id"])
            cur = by_id.get(cur.get("parent_node_id"))
            steps += 1
        prep = out.get(cur["node_id"]) if cur is not None else None
        for nid in path:
            out[nid] = prep
    return out


def _resolve_prep(sid, n, preps, graph):
    """(prep_id, note). `n` may be a prep id or an artifact id of the study;
    None means the study's first prep."""
    prep_ids = [p.get("prep_template_id") for p in preps]
    if n is None:
        return (prep_ids[0] if prep_ids else None), ""
    if n in prep_ids:
        return n, ""
    for node in graph:
        if node.get("kind") == "artifact" and node.get("artifact_id") == n:
            prep = node_preps(graph).get(node["node_id"])
            if prep is not None:
                return prep, f"{n} is an artifact id; showing its prep {prep}. "
    return None, ""


def _prep_dt(preps, prep_id):
    return next((p.get("data_type") for p in preps if p.get("prep_template_id") == prep_id), None)


def _match_data_type(value, available):
    """Canonical data type from the study's own list (case-insensitive), or via
    the synonym map ("shotgun" -> "Metagenomic"); None when it isn't one."""
    if not value:
        return None
    low = {a.lower(): a for a in available if a}
    if value.strip().lower() in low:
        return low[value.strip().lower()]
    for dt in detect_data_types([value]):
        if dt.lower() in low:
            return low[dt.lower()]
    return None


def _title(sid, header):
    return f'study {sid} "{header.get("study_title") or "untitled"}"'


# ── get_study_preps ─────────────────────────────────────────────────────────

def _tool_preps(sid, header, args):
    preps, graph = load_preps_and_graph(sid)
    available = sorted({p.get("data_type") for p in preps if p.get("data_type")})
    want = args.get("data_type")
    dt = _match_data_type(want, available)
    if want and dt is None:
        return ToolResult(text=f"{_title(sid, header)} has no {want!r} preps. Its data types: "
                               f"{', '.join(available) or 'none'}.",
                          label=f"Preps of study {sid}", detail=f"no {want} preps")
    samples_by_prep = {g["prep_id"]: g["num_samples"] for g in prep_groups(sid) if g["prep_id"] is not None}
    per_node = node_preps(graph)
    artifacts_by_prep = Counter(per_node[n["node_id"]] for n in graph
                                if n.get("kind") == "artifact" and not _archived(n))
    shown = [p for p in preps if not dt or p.get("data_type") == dt]

    samples_by_dt = Counter()
    for entries in prep_membership(sid).values():
        for t in {d for _pid, d in entries}:
            samples_by_dt[t] += 1
    preps_by_dt = Counter(p.get("data_type") for p in preps)
    lines = [f"{_title(sid, header)}: {len(preps)} preps, {header.get('num_samples') or '?'} samples.",
             "Data types: " + "; ".join(f"{t} — {preps_by_dt[t]} preps, {samples_by_dt.get(t, 0)} samples"
                                        for t in available)]
    if dt:
        lines.append(f"Showing the {len(shown)} {dt} preps.")
    lines.append("Preps (prep id · data type · samples · platform · target gene · status · artifacts):")
    for p in shown[:_PREP_ROWS]:
        pid = p.get("prep_template_id")
        lines.append(" · ".join(str(x) for x in (
            pid, p.get("data_type") or "?", f"{samples_by_prep.get(pid, 0)} samples",
            p.get("platform") or "—", p.get("target_gene") or "—",
            p.get("preprocessing_status") or "—", f"{artifacts_by_prep.get(pid, 0)} artifacts")))
    if len(shown) > _PREP_ROWS:
        lines.append(f"+{len(shown) - _PREP_ROWS} more preps" + ("" if dt else " (pass data_type to narrow)") + ".")
    lines.append("The user sees these preps as a table; clicking one shows its processing graph.")
    summary = f"{len(shown)} preps" + (f" · {dt}" if dt else f" · {len(available)} data types")
    return ToolResult(
        text=_clip("\n".join(lines)), label=f"Preps of study {sid}", detail=summary,
        ui_payload={"kind": "study_preps", "study_id": sid, "study_title": header.get("study_title"),
                    "data_type": dt, "result_summary": summary})


# ── show_study_samples ──────────────────────────────────────────────────────

def _tool_samples(sid, header, args):
    prep_arg, want = _int(args.get("prep_id")), args.get("data_type")
    prep_id, data_type, note = None, None, ""
    if prep_arg is not None:
        preps, graph = load_preps_and_graph(sid)
        prep_id, note = _resolve_prep(sid, prep_arg, preps, graph)
        if prep_id is None:
            return _err("show_study_samples", f"{_title(sid, header)} has no prep or artifact {prep_arg}.",
                        "unknown prep")
        data_type = _prep_dt(preps, prep_id)
        first, total = fetch_prep_samples(sid, prep_id, 10)
        ids = [s["sample_id"] for s in first]
        scope = f"prep {prep_id}" + (f" ({data_type})" if data_type else "")
    elif want:
        membership = prep_membership(sid)
        available = sorted({d for entries in membership.values() for _p, d in entries})
        data_type = _match_data_type(want, available)
        if data_type is None:
            return ToolResult(text=f"{_title(sid, header)} has no {want!r} samples. Its data types: "
                                   f"{', '.join(available) or 'none'}.",
                              label=f"Samples of study {sid}", detail=f"no {want} samples")
        all_ids = sorted(s for s, entries in membership.items() if any(d == data_type for _p, d in entries))
        ids, total = all_ids[:10], len(all_ids)
        scope = f"data type {data_type}"
    else:
        all_ids = list_study_sample_ids(sid)
        ids, total = all_ids[:10], len(all_ids)
        scope = "all samples"
    columns = study_columns(sid)
    lines = [f"{note}{_title(sid, header)} — {scope}: {total} samples.",
             "First samples: " + (", ".join(ids) or "none") + ("" if total <= len(ids) else ", …"),
             f"Metadata columns ({len(columns)}): " + ", ".join(columns[:60])
             + (f", … (+{len(columns) - 60} more)" if len(columns) > 60 else ""),
             "The user sees the sample list with each clicked sample's metadata beside it. "
             "For one sample's values call get_sample_metadata; to read values across samples "
             "call the study report tool."]
    summary = f"{total} samples · {scope}"
    return ToolResult(
        text=_clip("\n".join(lines)), label=f"Samples of study {sid}", detail=summary,
        ui_payload={"kind": "study_samples", "study_id": sid, "study_title": header.get("study_title"),
                    "prep_id": prep_id, "data_type": None if prep_id else data_type, "total": total,
                    "result_summary": summary})


# ── get_sample_metadata ─────────────────────────────────────────────────────

def _tool_sample_metadata(sid, header, args):
    sample_id = str(args.get("sample_id") or "").strip()
    if not sample_id:
        return _err("get_sample_metadata", "sample_id is required.", "no sample_id")
    fields = fetch_sample_fields(sid, sample_id)
    if fields is None and not sample_id.startswith(f"{sid}."):
        prefixed = f"{sid}.{sample_id}"           # Qiita sample ids carry the study prefix
        fields = fetch_sample_fields(sid, prefixed)
        if fields is not None:
            sample_id = prefixed
    if fields is None:
        return _err("get_sample_metadata", f"{_title(sid, header)} has no sample {sample_id!r}.",
                    "sample not found")
    in_preps = prep_membership(sid).get(sample_id, [])
    lines = [f"Sample {sample_id} of {_title(sid, header)} — "
             + ("in " + ", ".join(f"prep {p} ({d})" for p, d in in_preps) if in_preps else "in no prep")
             + f"; {len(fields)} fields:"]
    for k in sorted(fields):
        v = "" if fields[k] is None else str(fields[k])
        lines.append(f"{k}: {v[:_VALUE_MAX]}{'…' if len(v) > _VALUE_MAX else ''}")
    summary = f"{len(fields)} fields"
    return ToolResult(
        text=_clip("\n".join(lines)), label=f"Sample {sample_id}", detail=summary,
        ui_payload={"kind": "sample_metadata", "study_id": sid, "sample_id": sample_id,
                    "result_summary": summary})


# ── get_prep_graph ──────────────────────────────────────────────────────────

def _job_params(node):
    out = []
    for k, v in (node.get("command_params") or {}).items():
        s = str(v)
        if "/" in s or len(s) > 40:            # file paths and blobs stay out of the model's text
            continue
        out.append(f"{k}={s}")
        if len(out) == 3:
            break
    return out


def _node_line(n):
    if n.get("kind") == "job":
        params = _job_params(n)
        return f"job {n.get('command_name') or '?'}" + (f" ({', '.join(params)})" if params else "")
    files = len(n.get("filepaths") or [])
    return (f'artifact {n.get("artifact_id")} "{n.get("name") or ""}" '
            f'({n.get("artifact_type") or "?"}, {n.get("visibility") or "?"}) · {files} files')


def _tree_lines(nodes, limit):
    ids = {n["node_id"] for n in nodes}
    kids = {}
    for n in nodes:
        kids.setdefault(n.get("parent_node_id") if n.get("parent_node_id") in ids else None, []).append(n)

    def key(n):   # jobs by command, then artifacts by id, as the chart orders siblings
        if n.get("kind") == "job":
            return (0, n.get("command_name") or "")
        return (1, str(n.get("artifact_id") or "").zfill(12))
    lines, stack = [], [(n, 0) for n in sorted(kids.get(None, []), key=key, reverse=True)]
    while stack and len(lines) < limit:
        n, depth = stack.pop()
        lines.append("  " * depth + _node_line(n))
        stack.extend((c, depth + 1) for c in sorted(kids.get(n["node_id"], []), key=key, reverse=True))
    return lines


def _tool_graph(sid, header, args):
    preps, graph = load_preps_and_graph(sid)
    prep_id, note = _resolve_prep(sid, _int(args.get("prep_id")), preps, graph)
    if prep_id is None:
        return _err("get_prep_graph", f"{_title(sid, header)} has no prep or artifact {args.get('prep_id')}.",
                    "unknown prep")
    per_node = node_preps(graph)
    mine = [n for n in graph if per_node.get(n["node_id"]) == prep_id]
    live = [n for n in mine if not _archived(n)]
    archived = len(mine) - len(live)
    dt = _prep_dt(preps, prep_id)
    n_art = sum(1 for n in live if n.get("kind") == "artifact")
    lines = [f"{note}Prep {prep_id}" + (f" ({dt})" if dt else "") + f" of {_title(sid, header)}: "
             f"{n_art} artifacts, {len(live) - n_art} processing steps"
             + (f" ({archived} archived artifacts hidden, as on Qiita)" if archived else "") + "."]
    tree = _tree_lines(live, _GRAPH_NODES)
    lines += tree
    if len(live) > len(tree):
        lines.append(f"… +{len(live) - len(tree)} more nodes")
    others = [p.get("prep_template_id") for p in preps if p.get("prep_template_id") != prep_id]
    if others:
        lines.append("Other preps: " + ", ".join(str(p) for p in others[:20])
                     + (f", … (+{len(others) - 20})" if len(others) > 20 else ""))
    lines.append("The user sees this graph and can click any node for its files or parameters.")
    summary = f"prep {prep_id} · {len(live)} nodes"
    return ToolResult(
        text=_clip("\n".join(lines)), label=f"Processing graph of study {sid}", detail=summary,
        ui_payload={"kind": "prep_graph", "study_id": sid, "study_title": header.get("study_title"),
                    "prep_id": prep_id, "data_type": dt, "result_summary": summary})


# ── list_artifact_files ─────────────────────────────────────────────────────

def _tool_files(sid, header, args):
    preps, graph = load_preps_and_graph(sid)
    aid, note = _int(args.get("artifact_id")), ""
    node = next((n for n in graph if n.get("kind") == "artifact" and aid is not None
                 and n.get("artifact_id") == aid), None)
    if node is not None:
        nodes, prep_id = [node], node_preps(graph).get(node["node_id"])
    else:
        prep_arg = _int(args.get("prep_id")) if aid is None else aid   # an "artifact" id may be a prep id
        prep_id, note = _resolve_prep(sid, prep_arg, preps, graph)
        if prep_id is None:
            return _err("list_artifact_files", f"{_title(sid, header)} has no artifact or prep "
                                               f"{prep_arg}.", "unknown artifact")
        per_node = node_preps(graph)
        nodes = [n for n in graph if n.get("kind") == "artifact" and not _archived(n)
                 and per_node.get(n["node_id"]) == prep_id]
    lines = [f"{note}Files of {_title(sid, header)}" + (f", prep {prep_id}" if prep_id is not None else "")
             + f" — {len(nodes)} artifact{'s' if len(nodes) != 1 else ''}. "
             "Filenames only; the user sees full paths and download links in the widget."]
    total = 0
    for n in nodes:
        fps = n.get("filepaths") or []
        total += len(fps)
        if len(lines) >= _FILE_LINES:
            continue
        lines.append(f'artifact {n.get("artifact_id")} "{n.get("name") or ""}" '
                     f'({n.get("artifact_type") or "?"}, {n.get("visibility") or "?"}): {len(fps)} files')
        for fp in fps[:_FILES_PER_ARTIFACT]:
            lines.append(f"  {fp.get('filename')} ({fp.get('filepath_type') or '?'}, file id {fp.get('filepath_id')})")
        if len(fps) > _FILES_PER_ARTIFACT:
            lines.append(f"  … +{len(fps) - _FILES_PER_ARTIFACT} more files")
    if len(lines) >= _FILE_LINES:
        lines = lines[:_FILE_LINES] + ["… (more artifacts in the widget)"]
    summary = f"{total} files · {len(nodes)} artifact{'s' if len(nodes) != 1 else ''}"
    return ToolResult(
        text=_clip("\n".join(lines)), label=f"Files of study {sid}", detail=summary,
        ui_payload={"kind": "artifact_files", "study_id": sid, "study_title": header.get("study_title"),
                    "prep_id": prep_id, "artifact_ids": [n.get("artifact_id") for n in nodes],
                    "result_summary": summary})


# ── propose_aggregation_add ─────────────────────────────────────────────────

def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _file_rows(files, file_filter=None):
    """[(sample_id, artifact_id)] of a study's per-sample file map
    (helpers.sample_files.get_sample_files) that pass file_filter — the rows an
    add checks (routes/aggregation_routes.py _rows_of)."""
    return [(sample, e[2]) for sample, entries in files.items() for e in entries
            if file_filter is None or group_matches(file_filter, e[0], e[1], e[2])]


def _tool_propose_aggregation(sid, header, args, user_id):
    """Scope, counts and target for adding a study (or some of its data types /
    preps) to one of the user's aggregations. Writes nothing: the card's Add
    button posts the returned file_filter to POST /api/aggregations/<id>/studies."""
    files = get_sample_files(sid)
    available = sorted({e[0] for es in files.values() for e in es}
                       | {d for ds in prep_data_types(sid).values() for d in ds})
    data_types, unknown = [], []
    for v in _as_list(args.get("data_types")):
        dt = _match_data_type(str(v), available)
        (data_types if dt else unknown).append(dt or str(v))
    if unknown:
        return ToolResult(text=f"{_title(sid, header)} has no {', '.join(unknown)} data. Its data types: "
                               f"{', '.join(available) or 'none'}.",
                          label="Aggregation proposal", detail="unknown data type")
    prep_ids = sorted({p for p in (_int(x) for x in _as_list(args.get("prep_ids"))) if p is not None})
    blocked, warnings, artifacts = None, [], []
    if prep_ids:
        a2p, with_files = artifact_preps(sid), {e[2] for es in files.values() for e in es}
        known = set(a2p.values())
        bad = [p for p in prep_ids if p not in known]
        if bad:
            return ToolResult(text=f"{_title(sid, header)} has no prep {', '.join(map(str, bad))}.",
                              label="Aggregation proposal", detail="unknown prep")
        for p in prep_ids:
            mine = [a for a, prep in a2p.items() if prep == p and a in with_files]
            if not mine:
                blocked = f"prep {p} has no per-sample FASTQ/FASTA files"
            artifacts += mine
    scoped = bool(data_types or prep_ids)
    file_filter = ({"data_types": sorted(set(data_types)), "processing": [],
                    "artifacts": sorted({str(a) for a in artifacts})} if scoped else None)
    study_rows = _file_rows(files)
    rows = _file_rows(files, file_filter) if scoped else study_rows
    samples = len({s for s, _a in rows})
    if scoped and not rows and not blocked:
        blocked = "nothing in that scope has per-sample FASTQ/FASTA files"
    if not study_rows:
        warnings.append("the study has no per-sample FASTQ/FASTA files, so it would be added with no file rows")

    aggs = []
    for a in (list_aggregations(user_id) if user_id else []):
        if a.get("chat_id"):                     # a chat's temporary one: add_to_chat_aggregation's
            continue
        ids = {int(s["study_id"]) for s in a.get("studies") or []}
        aggs.append({"aggregation_id": a["aggregation_id"], "name": a["name"], "studies": len(ids),
                     "has_study": sid in ids, "full": len(ids) >= AGGREGATION_STUDIES_CAP})
    open_aggs = [a for a in aggs if not a["has_study"] and not a["full"]]
    want = str(args.get("aggregation_name") or "").strip()
    named = next((a for a in aggs if want and a["name"].lower() == want.lower()), None)
    suggest = None
    if named and named["has_study"]:
        warnings.append(f'"{named["name"]}" already has this study')
    elif named and named["full"]:
        warnings.append(f'"{named["name"]}" already holds {AGGREGATION_STUDIES_CAP} studies')
    elif named:
        suggest = {"aggregation_id": named["aggregation_id"], "name": named["name"]}
    elif want:
        warnings.append(f'there is no saved aggregation named "{want}" (new ones start as this chat\'s '
                        "aggregation: use add_to_chat_aggregation)")
    elif len(open_aggs) == 1:
        suggest = {"aggregation_id": open_aggs[0]["aggregation_id"], "name": open_aggs[0]["name"]}

    scope_txt = ", ".join(filter(None, [f"data type {', '.join(sorted(set(data_types)))}" if data_types else "",
                                        f"prep {', '.join(map(str, prep_ids))}" if prep_ids else ""])) or "the whole study"
    lines = ["Proposal only — nothing was added. The user must pick an aggregation and click Add on the card.",
             f"{_title(sid, header)}, scope {scope_txt}: {samples} samples, {len(rows)} file rows "
             f"(of {len(study_rows)} in the study)."]
    if blocked:
        lines.append(f"Cannot add: {blocked}.")
    if warnings:
        lines.append("Note: " + "; ".join(warnings) + ".")
    if aggs:
        lines.append("The user's aggregations: " + "; ".join(
            f'"{a["name"]}" ({a["studies"]} studies' + (", already has this study" if a["has_study"] else "")
            + (", full" if a["full"] else "") + ")" for a in aggs[:20]))
    else:
        lines.append("The user has no saved aggregations; add it to this chat's aggregation instead "
                     "(add_to_chat_aggregation).")
    if suggest:
        lines.append(f'Suggested target: "{suggest["name"]}"' + (" (new)" if suggest["aggregation_id"] is None else "") + ".")
    summary = f"{len(rows)} file rows · {scope_txt}"
    return ToolResult(
        text=_clip("\n".join(lines)), label="Aggregation proposal", detail=summary,
        ui_payload={"kind": "aggregation_proposal", "study_id": sid, "study_title": header.get("study_title"),
                    "scope": {"data_types": sorted(set(data_types)), "prep_ids": prep_ids},
                    "file_filter": file_filter,
                    "counts": {"samples": samples, "rows": len(rows), "study_rows": len(study_rows)},
                    "suggest": suggest, "blocked": blocked, "warnings": warnings,
                    "result_summary": summary})


# ── resolve_study ───────────────────────────────────────────────────────────

def _chat_studies(scope, chat_id):
    """The chat's own studies, pinned ones first: [{study_id, study_title,
    study_alias, pi_name, num_samples, data_types, pinned}]."""
    pinned = [int(sid) for sid in list_pinned_studies(chat_id, scope)] if chat_id else []   # plain ids
    if scope == SCOPE_PROJECT:
        project = get_project_studies_only(get_project_id_for_chat(chat_id) or "") or {}
        rows = project.get("studies") or []
    else:
        rows = _fetch_study_headers(pinned) if pinned else []
    out = [{"study_id": int(r["study_id"]), "study_title": r.get("study_title"),
            "study_alias": r.get("study_alias"), "pi_name": r.get("pi_name"),
            "num_samples": r.get("num_samples"), "data_types": r.get("data_types"),
            "pinned": int(r["study_id"]) in pinned} for r in rows]
    return sorted(out, key=lambda s: not s["pinned"])


_TITLES_TTL_SECONDS = 3600
_titles_cache = {}  # "rows" -> (fetched_at_epoch, [(study_id, title, alias)]); tests clear it


def _public_titles():
    """Every public study's (id, title, alias), memoized per worker for an hour."""
    hit = _titles_cache.get("rows")
    if hit and time.time() - hit[0] < _TITLES_TTL_SECONDS:
        return hit[1]
    rows = pooled_fetchall(
        f"SELECT s.study_id, s.study_title, s.study_alias FROM qiita.study s WHERE {_PUBLIC_ARTIFACT_EXISTS}", [])
    _titles_cache["rows"] = (time.time(), rows)
    return rows


def _acronym_candidates(text, limit):
    """Public studies whose title acronym the text uses ("AGP" -> American Gut
    Project), exact acronyms first. Text search can't find these: the acronym
    is rarely in a study's own title or abstract."""
    tokens = [t for t in identifying_tokens(text) if not t.isdigit() and len(t) >= 3]
    if not tokens:
        return []
    ranked = []
    for sid, title, alias in _public_titles():
        rank = acronym_rank({"study_title": title, "study_alias": alias}, tokens)
        if rank is not None:
            ranked.append((rank, sid))
    return _fetch_study_headers([sid for _r, sid in sorted(ranked)[:limit]])


def _search_candidates(text, limit):
    """Public studies matching the identifying words of `text` (the Browse
    box's text search, without the sample-metadata pass)."""
    words = " ".join(t for t in identifying_tokens(text) if not t.isdigit())
    if not words:
        return []
    plan = browse_query_to_sql(words)
    kws = plan.get("keywords") or []
    if not kws:
        return []
    expanded = expand_keyword_variants(kws)
    pi_sql, pi_params = (build_pi_required_filter(plan.get("resolved_pis") or [])
                         if plan.get("veto_applied") else (None, []))
    rows = search_studies_with_sql(
        custom_sql_where=plan.get("where_clause") or "1=1", params=plan.get("params") or [],
        limit=limit, relevance_keywords=expanded, pi_filter_sql=pi_sql,
        pi_filter_params=pi_params, title_phrase=plan.get("phrase"))
    return rows if isinstance(rows, list) else []


def _candidate(s, pinned=False):
    return {"study_id": int(s["study_id"]), "study_title": s.get("study_title"),
            "pi_name": s.get("pi_name"), "num_samples": s.get("num_samples"),
            "data_types": s.get("data_types"), "pinned": bool(s.get("pinned", pinned))}


def _tool_resolve_study(args, *, scope, chat_id):
    text = str(args.get("text") or "").strip()[:500]
    for_tool = args.get("for_tool") if isinstance(args.get("for_tool"), str) else None
    chat = _chat_studies(scope, chat_id)
    r = resolve_text(text, chat)
    sid = r["study_id"]
    if sid is not None and r["how"] == "explicit":
        allowed = ({s["study_id"] for s in chat} if scope == SCOPE_PROJECT else None)
        if (allowed is not None and sid not in allowed) or not is_study_public(sid):
            where = "in this project" if allowed is not None else "public"
            return ToolResult(text=f"Study {sid} is not {where}. Tell the user.", label="Study not found",
                              detail=f"study {sid} not {where}")
    if sid is not None:
        hit = next((s for s in chat if s["study_id"] == sid), None) or \
            (_fetch_study_headers([sid]) or [{"study_id": sid}])[0]
        title = hit.get("study_title") or ""
        why = "the study id in the request" if r["how"] == "explicit" else (
            "the chat's pinned study" if hit.get("pinned") else "the project's study")
        nxt = f" Now call {for_tool} with study_id={sid}." if for_tool else ""
        summary = f"Using study {sid} · {title}"
        return ToolResult(
            text=f'Resolved to study {sid} "{title}" ({why}).{nxt}', label="Study found",
            detail=f"study {sid}",
            ui_payload={"kind": "study_resolved", "study_id": sid, "study_title": title,
                        "result_summary": summary})

    candidates = [_candidate(s) for s in r["matched"]]
    if scope != SCOPE_PROJECT:
        seen, hits = {c["study_id"] for c in candidates}, []
        for s in _acronym_candidates(text, _CANDIDATES) + _search_candidates(text, _CANDIDATES + len(seen)):
            if int(s["study_id"]) not in seen:
                seen.add(int(s["study_id"]))
                hits.append(s)
        candidates += [_candidate(s) for s in hits[:_CANDIDATES]]
    elif not candidates:
        candidates = [_candidate(s) for s in chat[:8]]
    if not candidates:
        return ToolResult(
            text=f"No study matched {text!r}. Ask the user for the study id or a more specific name.",
            label="No matching study", detail="0 candidates")
    listing = "; ".join(f'{c["study_id"]} "{c["study_title"]}"' + (" (pinned)" if c["pinned"] else "")
                        for c in candidates)
    stop = f"Do not call {for_tool} yet. " if for_tool else ""
    summary = f"{len(candidates)} candidate stud{'y' if len(candidates) == 1 else 'ies'}"
    return ToolResult(
        text=(f"Not certain which study {text!r} means. {len(candidates)} candidates are shown to the "
              f"user to pick from: {listing}. {stop}Ask the user, in one sentence, to pick one."),
        label="Which study?", detail=summary,
        ui_payload={"kind": "study_choice", "for_tool": for_tool, "text": text,
                    "candidates": candidates, "result_summary": summary})
