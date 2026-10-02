"""Sample Aggregation routes: a user's named set of file rows, grouped by study,
plus one export (CSV for scripts, xlsx for spreadsheets, TSV) of the checked
rows — one per sample × artifact: study_id, sample_id, artifact_id, data_type,
processing, R1, R2, barcodes.

Selection is per row: a row is a (sample_id, artifact_id) pair, and the artifact
fixes its prep (helpers.aggregation_rows). Each row has its own checkbox; a
sample with no file under the study's filter is a placeholder row that can't be
checked. A study from before per-row selection is migrated lazily the first time
a route needs its selection (_ready).

Each study in an aggregation has its own saved file_filter (data types ×
processing steps × artifact ids; empty = any), edited from that study's
sample-table toolbar and applied only to that study's table and export rows.
It has two effects. Its data-type half also scopes the sample table itself:
a sample stays listed if it has no chosen type, or belongs to it by prep
membership (helpers.study_samples.prep_data_types) or by file (a data type
without a per-sample file, e.g. a 16S study with only Demultiplexed/BIOM
artifacts, is in scope but shows "—" for FASTQ/FASTA — see
helpers.sample_files.in_scope). The full filter (both halves) applies
everywhere a file is counted: FASTQ/FASTA, files-first order, Show filter and
"with files" count, the "select: with_files" bulk action, /file-facets, and
the export.

Auth (401) and CSRF (403) are enforced by helpers/auth_middleware.py for every
route here, so g.user_id is always set. Every mutation returns the full
aggregation so the frontend patches state from the response, never re-fetches.
"""

from flask import Response, g, jsonify, request

from run import app
from store import (
    AGGREGATION_STUDIES_CAP,
    list_aggregations,
    create_aggregation,
    get_aggregation,
    rename_aggregation,
    set_study_file_filter,
    delete_aggregation,
    add_study_to_aggregation,
    remove_study_from_aggregation,
    remove_rows_by_artifacts,
    save_chat_aggregation,
    set_aggregation_rows,
    migrate_study_rows,
    selected_rows_in,
    selected_by_study,
)
from helpers.fastq_manifest import (
    XLSX_MIMETYPE, count_fastq_artifacts, fetch_export_rows, group_matches, to_csv, to_xlsx,
)
from helpers.aggregation_rows import file_entries, order_rows
from helpers.sample_files import effective, facet_counts, get_sample_files, in_scope, page_files
from helpers.qiita_fetch import is_study_public
from helpers.study_samples import (
    artifact_preps, display_columns, fetch_samples_by_ids, list_study_sample_ids, matching_sample_ids,
    prep_data_types, prep_membership,
)

_MAX_IDS_PER_PATCH = 50_000
_SAMPLES_BODY_HELP = ('body must be {"add": [{"sample_id", "artifact_id"}, ...], "remove": [...]} or '
                      '{"select": "all" | "none" | "with_files" | "matching", "q": "..."}')
_SHOW_VALUES = ("all", "with_files", "without_files")
# {fastq availability int -> API string}; 0 (none) -> None, handled separately.
_FASTQ_LABEL = {2: "paired", 1: "single"}
_FILTER_KEYS = ("data_types", "processing", "artifacts")
_SORT_KEYS = ("prep", "artifact")
_FILTER_MAX_ITEMS, _FILTER_MAX_LEN = 50, 200
# Artifact picks can be many: a chat aggregation that mixes a data type with a
# prep stores the union as artifact ids (AGP's 16S alone is ~114 artifacts).
_FILTER_MAX_ARTIFACTS = 5000


def _rows_of(all_files, sample_ids, file_filter=None):
    """[(sample_id, artifact_id)] for these samples' file entries — only those
    passing file_filter when one is given."""
    return [(sid, e[2]) for sid in sample_ids for e in all_files.get(sid, [])
            if file_filter is None or group_matches(file_filter, e[0], e[1], e[2])]


def _parse_file_filter(value):
    """Validate a client file_filter → {"data_types": [...], "processing": [...], "artifacts": [...]}."""
    help_ = 'file_filter must be {"data_types": [str, ...], "processing": [str, ...], "artifacts": [str, ...]}'
    if not isinstance(value, dict) or set(value) - set(_FILTER_KEYS):
        raise ValueError(help_)
    out = {}
    for key in _FILTER_KEYS:
        items = value.get(key) or []
        cap = _FILTER_MAX_ARTIFACTS if key == "artifacts" else _FILTER_MAX_ITEMS
        if (not isinstance(items, list) or len(items) > cap
                or not all(isinstance(x, str) and 0 < len(x) <= _FILTER_MAX_LEN for x in items)):
            raise ValueError(help_)
        out[key] = sorted(set(items))
    return out


@app.route("/api/aggregations", methods=["GET"])
def api_list_aggregations():
    return jsonify({"aggregations": list_aggregations(g.user_id)})


@app.route("/api/aggregations", methods=["POST"])
def api_create_aggregation():
    name = ((request.get_json() or {}).get("name") or "").strip() or "Untitled"
    return jsonify(create_aggregation(g.user_id, name)), 201


@app.route("/api/aggregations/<aggregation_id>", methods=["GET"])
def api_get_aggregation(aggregation_id):
    """One aggregation — the chat re-reads it after a tool changed it server-side."""
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


@app.route("/api/aggregations/<aggregation_id>/save", methods=["POST"])
def api_save_chat_aggregation(aggregation_id):
    """Body {"name"}: keep a chat's temporary aggregation — named, detached from
    the chat, and listed in the Sample Aggregation tab from now on."""
    name = ((request.get_json() or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    agg = save_chat_aggregation(aggregation_id, g.user_id, name[:200])
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


def _artifact_list(value):
    """None (= every artifact) or a list of ints; ValueError otherwise."""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > _FILTER_MAX_ARTIFACTS:
        raise ValueError
    return [int(a) for a in value]


@app.route("/api/aggregations/<aggregation_id>/studies/<int:study_id>/undo-add", methods=["POST"])
def api_undo_chat_aggregation_add(aggregation_id, study_id):
    """Undo one add_to_chat_aggregation (helpers/aggregation_tools.py), from the
    `undo` its widget carries: {was_new, added_artifacts, prev_artifacts,
    prev_filter} (artifact id lists; null = every artifact). A new study is
    removed; an extended one loses the added artifacts' rows and gets its
    previous filter back."""
    body = request.get_json() or {}
    try:
        added, prev = _artifact_list(body.get("added_artifacts")), _artifact_list(body.get("prev_artifacts"))
        prev_filter = _parse_file_filter(body["prev_filter"]) if body.get("prev_filter") is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "added_artifacts / prev_artifacts must be lists of artifact ids or null, "
                                 "prev_filter a file filter"}), 400
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None or _study_in(agg, study_id) is None:
        return jsonify({"error": "Aggregation or study not found"}), 404
    if body.get("was_new"):
        return jsonify(remove_study_from_aggregation(aggregation_id, g.user_id, study_id))
    if prev is None:
        return jsonify({"error": "Nothing to undo: the study already held every file"}), 400
    if added is None:           # the add made it the whole study: keep only what it had
        remove_rows_by_artifacts(aggregation_id, g.user_id, study_id, prev, keep=True)
    else:
        remove_rows_by_artifacts(aggregation_id, g.user_id, study_id, added)
    ff = prev_filter or {"data_types": [], "processing": [], "artifacts": sorted(str(a) for a in prev)}
    return jsonify(set_study_file_filter(aggregation_id, g.user_id, study_id, ff))


@app.route("/api/aggregations/<aggregation_id>", methods=["PATCH"])
def api_update_aggregation(aggregation_id):
    """Body: {"name": ...}. Returns the full aggregation. The file filter is
    saved per study (PATCH …/studies/<study_id>)."""
    body = request.get_json() or {}
    if "file_filter" in body:
        return jsonify({"error": "file_filter is saved per study: "
                                 "PATCH /api/aggregations/<id>/studies/<study_id>"}), 400
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    agg = rename_aggregation(aggregation_id, g.user_id, name)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


@app.route("/api/aggregations/<aggregation_id>/studies/<int:study_id>", methods=["PATCH"])
def api_set_study_file_filter(aggregation_id, study_id):
    """Body: {"file_filter": {"data_types": [...], "processing": [...],
    "artifacts": [...]}} — that study's filter (empty lists = any). Returns the
    full aggregation."""
    body = request.get_json() or {}
    if "file_filter" not in body:
        return jsonify({"error": "file_filter required"}), 400
    try:
        file_filter = _parse_file_filter(body["file_filter"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    agg = set_study_file_filter(aggregation_id, g.user_id, study_id, file_filter)
    if agg is None:
        return jsonify({"error": "Aggregation or study not found"}), 404
    return jsonify(agg)


@app.route("/api/aggregations/<aggregation_id>/file-facets", methods=["GET"])
def api_aggregation_file_facets(aggregation_id):
    """`exportable` (checked rows under their own study's filter — whether the
    export will contain anything) and `selected` (all checked rows), for the
    tab header. With ?study_id=, also that study's Data type / Processing /
    Artifact picker options (sample counts, facet-style, under its own filter;
    Data type options include prep-only types with no per-sample file, e.g. a
    16S study with only Demultiplexed/BIOM artifacts) — 404 if the study is
    not in the aggregation."""
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    for s in list(agg["studies"]):
        agg = _ready(agg, s["study_id"])
    selected = selected_by_study(aggregation_id)
    exportable = 0
    for s in agg["studies"]:
        rows = selected.get(int(s["study_id"]))
        if rows:
            files = get_sample_files(s["study_id"])
            exportable += len(rows & set(_rows_of(files, files, s["file_filter"])))
    out = {"exportable": exportable, "selected": sum(len(rows) for rows in selected.values())}
    if request.args.get("study_id") is not None:
        try:
            study = _study_in(agg, int(request.args["study_id"]))
        except ValueError:
            study = None
        if study is None:
            return jsonify({"error": "Study not in aggregation"}), 404
        sid = study["study_id"]
        out["data_types"], out["processing"], out["artifacts"] = facet_counts(
            [get_sample_files(sid)], [prep_data_types(sid)], study["file_filter"])
    return jsonify(out)


@app.route("/api/aggregations/<aggregation_id>", methods=["DELETE"])
def api_delete_aggregation(aggregation_id):
    if not delete_aggregation(aggregation_id, g.user_id):
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify({"deleted": aggregation_id})


@app.route("/api/aggregations/<aggregation_id>/studies", methods=["POST"])
def api_add_study_to_aggregation(aggregation_id):
    """Add a whole study: every one of its (sample, artifact) rows starts checked. The body's
    `study` is the Browse card's header (title, abstract, PI, year, GOLD,
    counts), snapshotted for the tab's cards. An optional `file_filter` (the
    chat's aggregation card) adds only the rows it keeps and is saved as the
    study's filter in the same insert."""
    body = request.get_json() or {}
    study = body.get("study")
    if not study or study.get("study_id") is None:
        return jsonify({"error": "study with study_id required"}), 400
    file_filter = None
    if body.get("file_filter") is not None:
        try:
            file_filter = _parse_file_filter(body["file_filter"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    study_id = int(study["study_id"])
    if not is_study_public(study_id):
        return jsonify({"error": "Study is not public and cannot be added"}), 403
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    if len(agg["studies"]) >= AGGREGATION_STUDIES_CAP:
        return jsonify({"error": f"Aggregation has reached the maximum of {AGGREGATION_STUDIES_CAP} studies"}), 400
    files = get_sample_files(study_id)
    study = {**study, "num_samples": len(list_study_sample_ids(study_id))}
    agg = add_study_to_aggregation(aggregation_id, g.user_id, study, count_fastq_artifacts(study_id),
                                   _rows_of(files, files, file_filter), file_filter=file_filter)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


@app.route("/api/aggregations/<aggregation_id>/studies/<int:study_id>", methods=["DELETE"])
def api_remove_study_from_aggregation(aggregation_id, study_id):
    agg = remove_study_from_aggregation(aggregation_id, g.user_id, study_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


def _study_in(agg, study_id):
    return next((s for s in agg["studies"] if int(s["study_id"]) == int(study_id)), None)


def _ready(agg, study_id):
    """The aggregation with this study's selection on per-row storage: a study
    from before per-row selection is migrated (once) from its checked samples
    first, which changes its counts, so the aggregation is re-read."""
    study = _study_in(agg, study_id)
    if study is not None and not study.get("rows_v"):
        migrate_study_rows(agg["aggregation_id"], int(study_id), get_sample_files(study_id))
        agg = get_aggregation(agg["aggregation_id"], g.user_id)
    return agg


@app.route("/api/aggregations/<aggregation_id>/studies/<int:study_id>/samples", methods=["GET"])
def api_aggregation_study_samples(aggregation_id, study_id):
    """One page of the study's table rows. A row is a (sample, artifact) pair — the
    sample's file in one artifact, which fixes its prep — flagged with `selected`
    (SQLite, per row) and carrying that artifact's `file` {r1, r2, barcodes},
    `fastq`/`fasta`, `prep_id` and `prep_ids`; a sample with no file under the
    study's filter is one placeholder row (artifact_id null, never selected).
    ?offset= ?limit= (1-500 rows, default 200) ?q= substring filter on sample id
    or any metadata value ?show= all|with_files|without_files (default all)
    ?group=prep ?sort=prep|artifact ?dir=asc|desc (default asc).

    sort orders the rows by that id, rows with none last either way; ties keep
    the files-first / id order. group=prep clusters rows under their prep
    (ascending; a placeholder goes under each prep of its that passes the Data
    type filter, else "No prep"), and `groups` gives {prep_id, data_type, count}
    per group for the headers; sort=prep orders the groups, sort=artifact the
    rows inside each. `total` and `with_files` count rows.

    The study's saved file_filter first narrows which samples are in scope at
    all (in_scope: prep membership or file data type), then Show / with_files
    further narrow by file availability under the full filter.

    Paging happens in Python, not SQL: rows are sorted files-first (stable, so
    id order is preserved within each group), which a plain SQL LIMIT/OFFSET
    over qiita.sample_<id> cannot express."""
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    if _study_in(agg, study_id) is None:
        return jsonify({"error": "Study not in aggregation"}), 404
    study = _study_in(_ready(agg, study_id), study_id)
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        # <= 500 rows keeps selected_rows_in's IN (...) under SQLite's 999-bind ceiling.
        limit = min(500, max(1, int(request.args.get("limit", 200))))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid offset or limit"}), 400
    show = request.args.get("show", "all")
    if show not in _SHOW_VALUES:
        return jsonify({"error": f"show must be one of {', '.join(_SHOW_VALUES)}"}), 400
    group = request.args.get("group") or None
    if group not in (None, "prep"):
        return jsonify({"error": "group must be prep"}), 400
    sort = request.args.get("sort") or None
    direction = request.args.get("dir") or "asc"
    if sort not in (None, *_SORT_KEYS) or (sort and direction not in ("asc", "desc")):
        return jsonify({"error": "sort must be prep or artifact, dir asc or desc"}), 400
    desc = direction == "desc"
    q = (request.args.get("q") or "").strip() or None

    ids = matching_sample_ids(study_id, q) if q else list_study_sample_ids(study_id)
    all_files = get_sample_files(study_id)
    membership = prep_membership(study_id)
    prep_types = {sid: sorted({dt for _p, dt in preps}) for sid, preps in membership.items()}
    file_filter = study["file_filter"]
    # Data-type scope: a sample stays in the table if it has no chosen type
    # filter, or its prep membership / file data types intersect it. This is
    # broader than `files` below — a 16S study with no per-sample file (only
    # Demultiplexed/BIOM) is still in scope, shown with FASTQ/FASTA "—".
    ids = [i for i in ids if in_scope(prep_types.get(i, []), all_files.get(i, []), file_filter)]
    files = effective(all_files, file_filter)  # {sample_id: (fastq, fasta)} under the full filter
    with_files = sum(len(file_entries(all_files, i, file_filter)) for i in ids)
    if show == "with_files":
        ids = [i for i in ids if i in files]
    elif show == "without_files":
        ids = [i for i in ids if i not in files]
    # Stable sort: samples with a file first, id order preserved within each group.
    ids.sort(key=lambda i: i not in files)
    keys, groups = order_rows(ids, all_files, membership, artifact_preps(study_id), file_filter, group, sort, desc)
    page_keys = keys[offset:offset + limit]
    page = list(dict.fromkeys(k[0] for k in page_keys))  # one sample can span several rows
    by_id = {r[0]: r for r in fetch_samples_by_ids(study_id, page)}
    columns = display_columns(study_id)
    sel = selected_rows_in(aggregation_id, study_id, page)
    file_of = {(sid, f["artifact_id"]): f for sid, fs in page_files(study_id, page, all_files, file_filter).items()
               for f in fs}
    entry_of = {(sid, e[2]): e for sid in page for e in all_files.get(sid, [])}
    rows = []
    for sid, aid, prep_id in page_keys:
        r = by_id.get(sid)
        fields = dict(zip(columns, r[1:])) if r else {c: None for c in columns}
        entry = entry_of.get((sid, aid))
        if entry:
            dt, proc, _aid, fq, fa = entry
            f = file_of.get((sid, aid), {})
            row = {"fastq": _FASTQ_LABEL.get(fq), "fasta": bool(fa), "data_types": [dt], "file_data_types": [dt],
                   "processing": [proc], "prep_ids": [] if prep_id is None else [prep_id],
                   "file": {k: f.get(k, "") for k in ("r1", "r2", "barcodes")}}
        else:
            row = {"fastq": None, "fasta": False, "data_types": prep_types.get(sid, []), "file_data_types": [],
                   "processing": [], "file": None,
                   "prep_ids": [p for p, _dt in membership.get(sid, [])] if prep_id is None else [prep_id]}
        rows.append({"sample_id": sid, "artifact_id": aid, "prep_id": prep_id, "selected": (sid, aid) in sel,
                     **row, "fields": fields})
    resp = {
        "study_id": study_id, "total": len(keys), "offset": offset, "limit": limit,
        "columns": columns, "selected_count": study.get("selected_rows", 0),
        "with_files": with_files, "rows": rows,
    }
    if groups is not None:
        resp["groups"] = groups
    return jsonify(resp)


def _row_list(value):
    """[{"sample_id", "artifact_id"}] from a client body -> [(sample_id, artifact_id)]."""
    if value is None:
        return []
    if (not isinstance(value, list) or len(value) > _MAX_IDS_PER_PATCH
            or not all(isinstance(x, dict) and isinstance(x.get("sample_id"), str) and x["sample_id"]
                       and isinstance(x.get("artifact_id"), int) and not isinstance(x["artifact_id"], bool)
                       for x in value)):
        raise ValueError(_SAMPLES_BODY_HELP)
    return [(x["sample_id"], x["artifact_id"]) for x in value]


@app.route("/api/aggregations/<aggregation_id>/studies/<int:study_id>/samples", methods=["PATCH"])
def api_set_aggregation_rows(aggregation_id, study_id):
    """Edit which of the study's rows ((sample, artifact) pairs) are checked.
    Either explicit lists {"add": [{sample_id, artifact_id}], "remove": [...]} or
    a bulk {"select": "all" | "none" | "with_files" | "matching", "q": ...}.
    "all" (every row of the study) / "none" / "with_files" (the rows under the
    study's filter, narrowed by q — exactly what the export can contain)
    replace the whole selection; "matching" (requires q) adds every row of the
    samples the text filter hits. Pairs are stored as given; one that isn't a
    real row never resolves to a file in the export."""
    body = request.get_json() or {}
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    if _study_in(agg, study_id) is None:
        return jsonify({"error": "Study not in aggregation"}), 404
    study = _study_in(_ready(agg, study_id), study_id)
    try:
        select = body.get("select")
        if select is not None:
            q = (body.get("q") or "").strip()
            all_files = get_sample_files(study_id)
            if select == "all":
                kwargs = {"add": _rows_of(all_files, all_files), "clear": True}
            elif select == "none":
                kwargs = {"clear": True}
            elif select == "with_files":
                ids = matching_sample_ids(study_id, q) if q else list_study_sample_ids(study_id)
                kwargs = {"add": _rows_of(all_files, ids, study["file_filter"]), "clear": True}
            elif select == "matching" and q:
                kwargs = {"add": _rows_of(all_files, matching_sample_ids(study_id, q))}
            else:
                raise ValueError(_SAMPLES_BODY_HELP)
        else:
            kwargs = {"add": _row_list(body.get("add")), "remove": _row_list(body.get("remove"))}
            if not kwargs["add"] and not kwargs["remove"]:
                raise ValueError(_SAMPLES_BODY_HELP)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    agg = set_aggregation_rows(aggregation_id, g.user_id, study_id, **kwargs)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


def _export_rows(aggregation_id):
    """(rows, None) or (None, error response) for either export format."""
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return None, (jsonify({"error": "Aggregation not found"}), 404)
    for s in list(agg["studies"]):
        agg = _ready(agg, s["study_id"])
    selected = {sid: rows for sid, rows in selected_by_study(aggregation_id).items() if rows}
    if not selected:
        return None, (jsonify({"error": "No rows selected"}), 400)
    try:
        # Resolved at call time so tests can patch the module-level names.
        filters = {int(s["study_id"]): s["file_filter"] for s in agg["studies"]}
        return fetch_export_rows(selected, filters), None
    except ValueError as e:
        return None, (jsonify({"error": str(e)}), 404)


def _attachment(aggregation_id, ext):
    return {"Content-Disposition": f"attachment; filename=aggregation_{aggregation_id}_samples.{ext}"}


# ext -> (serializer, mimetype). csv is for scripts (a spreadsheet parses ids like
# 10317.000001062 as numbers, so the tab also offers xlsx, whose sample_id cells are
# text); tsv is the csv rows tab-separated; pooled (multiplexed) runs repeat the
# same paths for every sample of the prep.
_EXPORTS = {
    "csv": (to_csv, "text/csv"),
    "tsv": (lambda rows: to_csv(rows, "\t"), "text/tab-separated-values"),
    "xlsx": (to_xlsx, XLSX_MIMETYPE),
}


@app.route("/api/aggregations/<aggregation_id>/export.<ext>", methods=["GET"])
def download_aggregation_export(aggregation_id, ext):
    """Every checked sample's per-sample sequence files, each study under its
    own saved file_filter, as csv / tsv / xlsx — one row per sample × artifact, columns
    study_id, sample_id, artifact_id, data_type, processing, R1, R2, barcodes
    (blank when absent). Samples with no resolvable file are omitted."""
    if ext not in _EXPORTS:
        return jsonify({"error": "export must be csv, tsv or xlsx"}), 404
    rows, err = _export_rows(aggregation_id)
    if err:
        return err
    serialize, mimetype = _EXPORTS[ext]
    return Response(serialize(rows), mimetype=mimetype, headers=_attachment(aggregation_id, ext))
