"""Chat tools over the user's sample aggregations (dispatched from
helpers/study_tools.py :: execute_study_tool):

  add_to_chat_aggregation  — WRITES: adds a study, or some of its data types /
                             preps, to this chat's temporary aggregation
                             (created on first use), with an undo record.
  save_chat_aggregation    — names that aggregation and moves it to the
                             Sample Aggregation tab (a saved one).
  list_aggregations        — the user's saved aggregations and this chat's one.

A chat's temporary aggregation is an ordinary aggregation with chat_id /
chat_scope set (store/aggregation_crud.py), so the tab's sample table, filters
and export all work on it. Scopes are expressed as per-sample-file artifacts so
that adds union cleanly; the stored filter is compacted back to data types when
the union covers whole data types (which reads better in the tab).

Imports study_tools' small helpers; study_tools imports this module only inside
its dispatcher, so there is no import cycle.
"""
from store import (
    AGGREGATION_STUDIES_CAP, add_study_to_aggregation, create_aggregation, get_chat_aggregation,
    list_aggregations, save_chat_aggregation, set_aggregation_rows, set_study_file_filter,
)
from helpers.fastq_manifest import count_fastq_artifacts, group_matches
from helpers.sample_files import get_sample_files
from helpers.study_samples import artifact_preps, list_study_sample_ids, prep_data_types
from helpers.study_tools import _as_list, _clip, _file_rows, _int, _match_data_type, _title
from helpers.tool_result import ToolResult

CHAT_AGG_NAME = "Chat aggregation"
_EMPTY_FILTER = {"data_types": [], "processing": [], "artifacts": []}
_LIST_AGGS, _LIST_STUDIES = 20, 50


def _n(x):
    return f"{x or 0:,}"


def _rows_total(agg):
    return sum(s.get("selected_rows") or 0 for s in agg.get("studies") or [])


def _filter_text(ff):
    ff = ff or {}
    parts = []
    if ff.get("data_types"):
        parts.append("data type " + ", ".join(ff["data_types"]))
    if ff.get("processing"):
        parts.append("processing " + ", ".join(ff["processing"]))
    if ff.get("artifacts"):
        parts.append(f"{len(ff['artifacts'])} artifacts")
    return "; ".join(parts)


def _artifacts_of(files, ff):
    """The study's per-sample-file artifacts a filter keeps; None = every one
    (an empty filter)."""
    if not ff or not any(ff.get(k) for k in ("data_types", "processing", "artifacts")):
        return None
    return {e[2] for es in files.values() for e in es if group_matches(ff, e[0], e[1], e[2])}


def _compact_filter(arts, art_dt):
    """The filter to store for artifact set `arts`: None for the whole study, a
    data-type filter when `arts` is exactly some data types' artifacts, else
    the artifact ids."""
    if arts is None or arts >= set(art_dt):
        return None
    full = {dt for dt in set(art_dt.values()) if all(a in arts for a, d in art_dt.items() if d == dt)}
    if {a for a, d in art_dt.items() if d in full} == arts:
        return {**_EMPTY_FILTER, "data_types": sorted(full)}
    return {**_EMPTY_FILTER, "artifacts": sorted(str(a) for a in arts)}


def tool_add_to_chat_aggregation(sid, header, args, *, scope, chat_id, user_id):
    if not user_id or not chat_id:
        return ToolResult(text="A chat aggregation needs a signed-in chat.", label="Chat aggregation",
                          detail="no chat")
    files = get_sample_files(sid)
    art_dt = {e[2]: e[0] for es in files.values() for e in es}
    available = sorted(set(art_dt.values()) | {d for ds in prep_data_types(sid).values() for d in ds})
    data_types, unknown = [], []
    for v in _as_list(args.get("data_types")):
        dt = _match_data_type(str(v), available)
        (data_types if dt else unknown).append(dt or str(v))
    if unknown:
        return ToolResult(text=f"{_title(sid, header)} has no {', '.join(unknown)} data. Its data types: "
                               f"{', '.join(available) or 'none'}. Nothing was added.",
                          label="Chat aggregation", detail="unknown data type")
    prep_ids = sorted({p for p in (_int(x) for x in _as_list(args.get("prep_ids"))) if p is not None})
    scope_arts = None                                       # None = the whole study
    if data_types or prep_ids:
        scope_arts = {a for a, d in art_dt.items() if d in data_types}
        if prep_ids:
            a2p = artifact_preps(sid)
            bad = [p for p in prep_ids if p not in set(a2p.values())]
            if bad:
                return ToolResult(text=f"{_title(sid, header)} has no prep {', '.join(map(str, bad))}. "
                                       "Nothing was added.", label="Chat aggregation", detail="unknown prep")
            scope_arts |= {a for a, p in a2p.items() if p in prep_ids and a in art_dt}
        if not scope_arts:
            return ToolResult(text=f"Nothing in that scope of {_title(sid, header)} has per-sample FASTQ/FASTA "
                                   "files, so nothing was added.", label="Chat aggregation", detail="nothing to add")
    scope_txt = ", ".join(filter(None, [f"data type {', '.join(data_types)}" if data_types else "",
                                        f"prep {', '.join(map(str, prep_ids))}" if prep_ids else ""])) or "the whole study"

    agg = get_chat_aggregation(user_id, chat_id, scope)
    started = agg is None
    if started:
        agg = create_aggregation(user_id, CHAT_AGG_NAME, chat_id=chat_id, chat_scope=scope)
    aid = agg["aggregation_id"]
    present = next((s for s in agg["studies"] if int(s["study_id"]) == sid), None)
    notes = []
    if present is None:
        if len(agg["studies"]) >= AGGREGATION_STUDIES_CAP:
            return ToolResult(text=f"This chat's aggregation already holds {AGGREGATION_STUDIES_CAP} studies, the "
                                   "most it can. Nothing was added.", label="Chat aggregation", detail="full")
        ff = _compact_filter(scope_arts, art_dt)
        rows = _file_rows(files, ff)
        study = {**header, "study_id": sid, "num_samples": len(list_study_sample_ids(sid))}
        agg = add_study_to_aggregation(aid, user_id, study, count_fastq_artifacts(sid), rows, file_filter=ff)
        undo = {"was_new": True, "added_artifacts": None, "prev_artifacts": None, "prev_filter": None}
        if not art_dt:
            notes.append("the study has no per-sample FASTQ/FASTA files, so it adds samples but no file rows")
    else:
        prev_filter = present.get("file_filter") or _EMPTY_FILTER
        prev_arts = _artifacts_of(files, prev_filter)
        if prev_arts is None:
            return ToolResult(text=f"This chat's aggregation already holds every file of {_title(sid, header)}. "
                                   "Nothing was added.", label="Chat aggregation", detail="already there")
        added = (set(art_dt) if scope_arts is None else scope_arts) - prev_arts
        if not added:
            return ToolResult(text=f"This chat's aggregation already holds that part of {_title(sid, header)}. "
                                   "Nothing was added.", label="Chat aggregation", detail="already there")
        rows = [r for r in _file_rows(files) if r[1] in added]
        set_study_file_filter(aid, user_id, sid, _compact_filter(prev_arts | added, art_dt) or _EMPTY_FILTER)
        agg = set_aggregation_rows(aid, user_id, sid, add=rows)
        undo = {"was_new": False, "added_artifacts": sorted(added), "prev_artifacts": sorted(prev_arts),
                "prev_filter": prev_filter}

    studies, total = len(agg["studies"]), _rows_total(agg)
    lead = "Started this chat's aggregation with" if started else "Added"
    lines = [f"{lead} {_title(sid, header)} ({scope_txt}): +{_n(len(rows))} file rows. This chat's aggregation now "
             f"holds {studies} stud{'y' if studies == 1 else 'ies'}, {_n(total)} checked rows.",
             "It is already added — the user can Undo on the card, export it, or save it as a named aggregation."]
    if notes:
        lines.append("Note: " + "; ".join(notes) + ".")
    summary = f"+{_n(len(rows))} rows · {scope_txt}"
    return ToolResult(
        text=_clip("\n".join(lines)), label="Added to this chat's aggregation", detail=summary,
        ui_payload={"kind": "chat_aggregation_update", "aggregation_id": aid, "study_id": sid,
                    "study_title": header.get("study_title"), "added_rows": len(rows), "scope": scope_txt,
                    "totals": {"studies": studies, "rows": total}, "undo": undo, "notes": notes,
                    "updated_at": agg.get("updated_at"), "result_summary": summary})


def tool_save_chat_aggregation(args, *, scope, chat_id, user_id):
    name = str(args.get("name") or "").strip()[:200]
    if not name:
        return ToolResult(text="A name is required to save this chat's aggregation.", label="Save aggregation",
                          detail="no name")
    agg = get_chat_aggregation(user_id, chat_id, scope) if user_id and chat_id else None
    if agg is None:
        return ToolResult(text="This chat has no aggregation yet — add studies to it first.",
                          label="Save aggregation", detail="nothing to save")
    saved = save_chat_aggregation(agg["aggregation_id"], user_id, name)
    studies, total = len(saved["studies"]), _rows_total(saved)
    summary = f'"{name}" · {studies} studies · {_n(total)} rows'
    return ToolResult(
        text=(f'Saved this chat\'s aggregation as "{name}" ({studies} studies, {_n(total)} checked rows). It is now '
              "in the Sample Aggregation tab; the next add starts a new chat aggregation."),
        label="Saved aggregation", detail=summary,
        ui_payload={"kind": "aggregation_saved", "aggregation_id": saved["aggregation_id"], "name": name,
                    "updated_at": saved.get("updated_at"), "result_summary": summary})


def _agg_lines(a):
    studies = a.get("studies") or []
    lines = [f'"{a["name"]}" — {len(studies)} stud{"y" if len(studies) == 1 else "ies"}, '
             f'{_n(_rows_total(a))} rows checked, updated {(a.get("updated_at") or "")[:10]}']
    for s in studies[:_LIST_STUDIES]:
        if s.get("rows_v"):
            rows = f'{_n(s.get("selected_rows"))} of {_n(s.get("file_rows"))} file rows checked'
        else:
            rows = f'{_n(s.get("selected_rows"))} samples checked'
        ft = _filter_text(s.get("file_filter"))
        lines.append(f'  {s["study_id"]} "{s.get("study_title") or ""}": {rows}' + (f"; filter: {ft}" if ft else ""))
    if len(studies) > _LIST_STUDIES:
        lines.append(f"  … +{len(studies) - _LIST_STUDIES} more studies")
    return lines


def tool_list_aggregations(args, *, scope, chat_id, user_id):
    if not user_id:
        return ToolResult(text="Aggregations need a signed-in user.", label="Aggregations", detail="no user")
    aggs = list_aggregations(user_id)
    mine = next((a for a in aggs if chat_id and a.get("chat_id") == chat_id and a.get("chat_scope") == scope), None)
    saved = [a for a in aggs if not a.get("chat_id")]
    want = str(args.get("name") or "").strip().lower()
    if want:
        exact = [a for a in saved if a["name"].lower() == want]
        saved = exact or [a for a in saved if want in a["name"].lower()]
        if not saved:
            names = ", ".join(f'"{a["name"]}"' for a in aggs if not a.get("chat_id")) or "none"
            return ToolResult(text=f'The user has no saved aggregation named "{args.get("name")}". '
                                   f"Their saved aggregations: {names}.", label="Aggregations", detail="no match")
        mine = None
    lines = []
    if mine:
        lines += ["This chat's aggregation (temporary — not in the tab until saved):"] + _agg_lines(mine)
    lines.append(f"The user's saved aggregations ({len(saved)}, most recently updated first):" if saved
                 else "The user has no saved aggregations.")
    for a in saved[:_LIST_AGGS]:
        lines += _agg_lines(a)
    if len(saved) > _LIST_AGGS:
        lines.append(f"… +{len(saved) - _LIST_AGGS} more aggregations")
    lines.append("The user sees these as a list and can open any of them in the Sample Aggregation tab.")
    summary = f"{len(saved)} saved" + (" · this chat's" if mine else "")
    return ToolResult(
        text=_clip("\n".join(lines)), label="Your aggregations", detail=summary,
        ui_payload={"kind": "aggregation_list", "aggregation_ids": [a["aggregation_id"] for a in saved[:_LIST_AGGS]],
                    "chat_aggregation_id": mine["aggregation_id"] if mine else None,
                    "name": args.get("name") or None, "result_summary": summary})


def execute(name, args, *, scope, chat_id, user_id):
    """The aggregation tools that take no study."""
    if name == "save_chat_aggregation":
        return tool_save_chat_aggregation(args, scope=scope, chat_id=chat_id, user_id=user_id)
    return tool_list_aggregations(args, scope=scope, chat_id=chat_id, user_id=user_id)
