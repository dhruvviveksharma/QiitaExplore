"""export_table — a CSV / TSV file made from the chat, downloaded from its card
(frontend js/chat_export_widget.js).

Sources:
  samples      one study's sample metadata: every column, or `columns`; only the
               samples of a prep (`prep_id`) or a data type when given.
  files        one study's public artifact files with full paths (filters:
               prep_id, data_type, artifact_type, file_type).
  aggregation  the existing aggregation export (this chat's, or a saved one by
               name): a link only, nothing stored.
  rows         a table the model writes from tool results, at most 2,000 x 50.

samples / files keep their parameters and are rebuilt at download, so the file is
always current; rows are stored as given. chat_exports rows (store/export_crud.py)
go with their chat; GET /api/chat-exports/<id>.<csv|tsv> (routes/export_routes.py)
serves them to their owner only.
"""
import csv
import io
import json
import re

from store import create_export, get_chat_aggregation, list_aggregations
from helpers.study_detail import load_preps_and_graph
from helpers.study_samples import fetch_sample_table, list_study_sample_ids, prep_membership, study_columns
from helpers.study_tools import _err, _int, check_study, node_preps
from helpers.tool_result import ToolResult

TOOL = "export_table"
SOURCES = ("samples", "files", "aggregation", "rows")
FORMATS = {"csv": ("text/csv", ","), "tsv": ("text/tab-separated-values", "\t")}
FILE_COLUMNS = ["study_id", "prep_id", "data_type", "artifact_id", "artifact_type",
                "artifact_name", "file_type", "filename", "path"]
_MAX_ROWS, _MAX_COLS, _CELL_MAX = 2000, 50, 2000
_PREVIEW_ROWS, _PREVIEW_COLS, _PREVIEW_CELL = 5, 8, 80


def _cell(value, limit=_CELL_MAX):
    if value is None:
        return ""
    text = value if isinstance(value, str) else (
        json.dumps(value) if isinstance(value, (dict, list)) else str(value))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _safe_name(name, default):
    clean = re.sub(r"[^A-Za-z0-9._ -]+", "", name or "").strip().replace(" ", "_")[:80]
    return clean or default


def serialize(columns, rows, fmt):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=FORMATS[fmt][1], lineterminator="\n")
    w.writerow(columns)
    w.writerows(rows)
    return buf.getvalue()


# ── samples ─────────────────────────────────────────────────────────────────

def _matching_samples(sid, spec):
    """The study's sample ids, narrowed to a prep and / or data type when given."""
    ids = list_study_sample_ids(sid)
    prep_id, dt = spec.get("prep_id"), (spec.get("data_type") or "").casefold()
    if prep_id is None and not dt:
        return ids
    membership = prep_membership(sid)
    return [s for s in ids if any((prep_id is None or pid == prep_id) and (not dt or (t or "").casefold() == dt)
                                  for pid, t in membership.get(s, []))]


def _sample_columns(sid, wanted):
    have = study_columns(sid)
    if not wanted:
        return have
    by_key = {c.casefold(): c for c in have}
    return [by_key[w.casefold()] for w in wanted if isinstance(w, str) and w.casefold() in by_key]


def _samples_table(spec, limit=None):
    """(columns, rows, number of matching samples)."""
    sid = spec["study_id"]
    columns = _sample_columns(sid, spec.get("columns"))
    ids = _matching_samples(sid, spec)
    narrowed = spec.get("prep_id") is not None or bool(spec.get("data_type"))
    rows = fetch_sample_table(sid, columns, sample_ids=ids if narrowed else None, limit=limit)
    return ["sample_id"] + columns, [list(r) for r in rows], len(ids)


# ── files ───────────────────────────────────────────────────────────────────

def _files_table(spec):
    """(FILE_COLUMNS, one row per file of the study's public artifacts)."""
    sid = spec["study_id"]
    _preps, graph = load_preps_and_graph(sid)
    per_node = node_preps(graph)
    dt, atype, ftype = ((spec.get(k) or "").casefold() for k in ("data_type", "artifact_type", "file_type"))
    rows = []
    for n in graph:
        if n.get("kind") != "artifact" or n.get("visibility") != "public":
            continue
        pid = per_node.get(n["node_id"])
        if (spec.get("prep_id") is not None and pid != spec["prep_id"]) \
                or (dt and (n.get("data_type") or "").casefold() != dt) \
                or (atype and (n.get("artifact_type") or "").casefold() != atype):
            continue
        for fp in n.get("filepaths") or []:
            name = fp.get("filename") or ""
            if ftype and (fp.get("filepath_type") or "").casefold() != ftype \
                    and not name.casefold().endswith("." + ftype):
                continue
            rows.append([sid, pid, n.get("data_type"), n.get("artifact_id"), n.get("artifact_type"),
                         n.get("name"), fp.get("filepath_type"), name, fp.get("full_path")])
    rows.sort(key=lambda r: (r[1] is None, r[1] or 0, r[3] or 0, r[7]))
    return list(FILE_COLUMNS), rows


def build_table(export):
    """(columns, rows) of a stored export, for the download route."""
    source, spec = export["source"], export["spec"]
    if source == "rows":
        return export["rows"]["columns"], export["rows"]["rows"]
    if source == "samples":
        columns, rows, _n = _samples_table(spec)
        return columns, rows
    if source == "files":
        return _files_table(spec)
    raise ValueError(f"unknown export source {source!r}")


# ── the tool ────────────────────────────────────────────────────────────────

def _model_rows(args):
    """(columns, rows, error) for source "rows"."""
    columns, rows = args.get("columns"), args.get("rows")
    if not isinstance(columns, list) or not columns:
        return None, None, "source \"rows\" needs columns: a list of column names."
    if len(columns) > _MAX_COLS:
        return None, None, f"At most {_MAX_COLS} columns."
    if not isinstance(rows, list) or not rows:
        return None, None, "source \"rows\" needs rows: a list of lists, one per row."
    if len(rows) > _MAX_ROWS:
        return None, None, f"At most {_MAX_ROWS} rows; for a study's samples or files use those sources."
    if not all(isinstance(r, list) for r in rows):
        return None, None, "Each row must be a list of cell values."
    width = len(columns)
    out = [[_cell(v) for v in r[:width]] + [""] * max(0, width - len(r)) for r in rows]
    return [_cell(c, 200) for c in columns], out, None


def _aggregation(args, fmt, *, scope, chat_id, user_id):
    wanted = (args.get("aggregation") or "").strip()
    if wanted:
        saved = [a for a in list_aggregations(user_id) if not a.get("chat_id")]
        key = wanted.casefold()
        match = [a for a in saved if (a.get("name") or "").casefold() == key] \
            or [a for a in saved if key in (a.get("name") or "").casefold()]
        if len(match) != 1:
            names = ", ".join(f'"{a.get("name")}"' for a in (match or saved)) or "none yet"
            return _err(TOOL, (f'"{wanted}" matches several aggregations: {names}. Ask which one.' if match
                               else f'No saved aggregation named "{wanted}". Saved: {names}.'), "aggregation not found")
        agg = match[0]
    else:
        agg = get_chat_aggregation(user_id, chat_id, scope)
        if not agg:
            return _err(TOOL, "This chat has no aggregation yet: add studies to it first, or name a saved one.",
                        "no aggregation")
    name = "This chat's aggregation" if agg.get("chat_id") else agg.get("name")
    n = len(agg.get("studies") or [])
    summary = f"{name} · {fmt}"
    return ToolResult(
        text=(f'The card links the {fmt.upper()} export of {name} ({n} stud{"y" if n == 1 else "ies"}): one row per '
              "checked sample x sequence file (study_id, sample_id, artifact_id, data_type, processing, R1, R2, "
              "barcodes). Nothing new was stored."),
        label=f"Export of {name}", detail=summary,
        ui_payload={"kind": "table_export", "source": "aggregation", "aggregation_id": agg["aggregation_id"],
                    "name": name, "format": fmt, "n_studies": n, "result_summary": summary})


def execute(args, *, scope, chat_id, user_id):
    if not user_id or not chat_id:
        return _err(TOOL, "Exports need a signed-in user in a chat.", "no user")
    source = (args.get("source") or "").strip().lower()
    fmt = (args.get("format") or "csv").strip().lower()
    if source not in SOURCES:
        return _err(TOOL, f"source must be one of {', '.join(SOURCES)}.", "bad source")
    if fmt not in FORMATS:
        return _err(TOOL, "format must be csv or tsv.", "bad format")
    if source == "aggregation":
        return _aggregation(args, fmt, scope=scope, chat_id=chat_id, user_id=user_id)

    spec, stored, sid = {}, None, None
    if source == "rows":
        columns, rows, err = _model_rows(args)
        if err:
            return _err(TOOL, err, "bad rows")
        stored, n_rows, preview, default = {"columns": columns, "rows": rows}, len(rows), rows, "chat_table"
    else:
        sid, _header, err = check_study(TOOL, args.get("study_id"), scope=scope, chat_id=chat_id)
        if err:
            return err
        spec = {"study_id": sid}
        if _int(args.get("prep_id")) is not None:
            spec["prep_id"] = _int(args.get("prep_id"))
        for key in ("data_type",) + (("artifact_type", "file_type") if source == "files" else ()):
            if (args.get(key) or "").strip():
                spec[key] = args[key].strip()
        if source == "samples":
            if isinstance(args.get("columns"), list) and args["columns"]:
                spec["columns"] = [str(c) for c in args["columns"]][:300]
            columns, preview, n_rows = _samples_table(spec, limit=_PREVIEW_ROWS)
            if spec.get("columns") and len(columns) == 1:
                have = ", ".join(study_columns(sid)[:25])
                return _err(TOOL, f"None of those columns exist in study {sid}. Some it has: {have}.", "no such columns")
        else:
            columns, rows = _files_table(spec)
            n_rows, preview = len(rows), rows
        default = f"study_{sid}_{source}"
        if n_rows == 0:
            return _err(TOOL, f"No {'samples' if source == 'samples' else 'public files'} match those filters "
                              f"in study {sid}.", "nothing matches")

    name = _safe_name(args.get("name"), default)
    export_id = create_export(user_id, chat_id, scope, name, source, spec, stored)
    other = "TSV" if fmt == "csv" else "CSV"
    shown = ", ".join(columns[:12]) + (f", … (+{len(columns) - 12})" if len(columns) > 12 else "")
    summary = f"{n_rows} rows · {len(columns)} columns · {fmt}"
    return ToolResult(
        text=(f'Export ready: "{name}.{fmt}", {n_rows} rows x {len(columns)} columns ({shown}). The user downloads '
              f"it from the card (it also offers {other}); don't paste its contents into the reply."),
        label=f"Prepared {name}.{fmt}", detail=summary,
        ui_payload={"kind": "table_export", "source": source, "export_id": export_id, "name": name,
                    "format": fmt, "study_id": sid, "n_rows": n_rows, "n_columns": len(columns),
                    "columns": [_cell(c, _PREVIEW_CELL) for c in columns[:_PREVIEW_COLS]],
                    "preview": [[_cell(v, _PREVIEW_CELL) for v in r[:_PREVIEW_COLS]] for r in preview[:_PREVIEW_ROWS]],
                    "result_summary": summary})
