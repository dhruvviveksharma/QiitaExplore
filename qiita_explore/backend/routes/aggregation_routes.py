"""Sample Aggregation routes: a user's named set of samples, grouped by study,
plus one export (CSV for scripts, xlsx for spreadsheets, TSV) of the checked
samples' per-sample sequence files — one row per sample × artifact: study_id,
sample_id, artifact_id, data_type, processing, R1, R2, barcodes.

The aggregation's saved file_filter (data types × processing steps × artifact
ids; empty = any) has two effects. Its data-type half also scopes the sample table itself:
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
    set_aggregation_file_filter,
    delete_aggregation,
    add_study_to_aggregation,
    remove_study_from_aggregation,
    set_aggregation_samples,
    selected_in,
    selected_by_study,
)
from helpers.fastq_manifest import (
    XLSX_MIMETYPE, count_fastq_artifacts, fetch_export_rows, group_matches, to_csv, to_xlsx,
)
from helpers.sample_files import effective, facet_counts, get_sample_files, in_scope, page_files
from helpers.qiita_fetch import is_study_public
from helpers.study_samples import (
    display_columns, fetch_samples_by_ids, list_study_sample_ids, matching_sample_ids, prep_data_types,
    prep_membership,
)

_MAX_IDS_PER_PATCH = 50_000
_SAMPLES_BODY_HELP = ('body must be {"add": [...], "remove": [...]} or '
                      '{"select": "all" | "none" | "with_files" | "matching", "q": "..."}')
_SHOW_VALUES = ("all", "with_files", "without_files")
# {fastq availability int -> API string}; 0 (none) -> None, handled separately.
_FASTQ_LABEL = {2: "paired", 1: "single"}
_FILTER_KEYS = ("data_types", "processing", "artifacts")
_SORT_KEYS = ("prep", "artifact")
_FILTER_MAX_ITEMS, _FILTER_MAX_LEN = 50, 200


def _sort_key(values, desc):
    """Sort key for a sample's prep / artifact ids: its lowest id ascending, its
    highest descending (so the id shown at the top matches the arrow), and
    samples with none last in both directions."""
    if not values:
        return (1, 0)
    return (0, -max(values) if desc else min(values))


def _order_pairs(ids, membership, all_files, file_filter, group, sort, desc):
    """(pairs, groups): the (sample_id, prep_id) rows to page, and — when grouped —
    the header info [{prep_id, data_type, count}] (else None). `ids` arrive
    files-first / id ordered and every sort here is stable, so ties keep that
    order. Ungrouped, each sample is one row (prep_id None). Grouped, a sample
    is one row under every prep that passes the Data type filter (or under None,
    "No prep"); sort=prep orders the groups, sort=artifact the rows inside each."""
    if sort == "artifact" or (sort == "prep" and not group):
        def values(i):
            if sort == "prep":
                return [p for p, _dt in membership.get(i, [])]
            return [e[2] for e in all_files.get(i, []) if group_matches(file_filter, e[0], e[1], e[2])]
        ids = sorted(ids, key=lambda i: _sort_key(values(i), desc))
    if not group:
        return [(i, None) for i in ids], None
    want_dts = file_filter.get("data_types") or []
    pairs = []
    for i in ids:
        preps = [p for p, dt in membership.get(i, []) if not want_dts or dt in want_dts]
        pairs += [(i, p) for p in (preps or [None])]
    group_desc = desc and sort == "prep"
    pairs.sort(key=lambda t: _sort_key([] if t[1] is None else [t[1]], group_desc))
    prep_dt = {p: dt for preps in membership.values() for p, dt in preps}
    counts = {}
    for _i, p in pairs:
        counts[p] = counts.get(p, 0) + 1
    return pairs, [{"prep_id": p, "data_type": prep_dt.get(p), "count": n} for p, n in counts.items()]


def _parse_file_filter(value):
    """Validate a client file_filter → {"data_types": [...], "processing": [...], "artifacts": [...]}."""
    help_ = 'file_filter must be {"data_types": [str, ...], "processing": [str, ...], "artifacts": [str, ...]}'
    if not isinstance(value, dict) or set(value) - set(_FILTER_KEYS):
        raise ValueError(help_)
    out = {}
    for key in _FILTER_KEYS:
        items = value.get(key) or []
        if (not isinstance(items, list) or len(items) > _FILTER_MAX_ITEMS
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


@app.route("/api/aggregations/<aggregation_id>", methods=["PATCH"])
def api_update_aggregation(aggregation_id):
    """Body: {"name": ...} and/or {"file_filter": {"data_types": [...],
    "processing": [...], "artifacts": [...]}}. Returns the full aggregation."""
    body = request.get_json() or {}
    if "name" not in body and "file_filter" not in body:
        return jsonify({"error": "name or file_filter required"}), 400
    try:
        file_filter = _parse_file_filter(body["file_filter"]) if "file_filter" in body else None
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    name = (body.get("name") or "").strip() if "name" in body else None
    if name == "":
        return jsonify({"error": "name required"}), 400
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is not None and name is not None:
        agg = rename_aggregation(aggregation_id, g.user_id, name)
    if agg is not None and file_filter is not None:
        agg = set_aggregation_file_filter(aggregation_id, g.user_id, file_filter)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


@app.route("/api/aggregations/<aggregation_id>/file-facets", methods=["GET"])
def api_aggregation_file_facets(aggregation_id):
    """Options for the Data type / Processing / Artifact pickers across the aggregation's
    studies (sample counts, facet-style; Data type options include prep-only
    types with no per-sample file, e.g. a 16S study with only
    Demultiplexed/BIOM artifacts), plus `exportable`: how many checked
    samples have a file under the saved filter — i.e. whether the export
    will contain anything."""
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    file_filter = agg["file_filter"]
    maps = {int(s["study_id"]): get_sample_files(s["study_id"]) for s in agg["studies"]}
    preps = {int(s["study_id"]): prep_data_types(s["study_id"]) for s in agg["studies"]}
    data_types, processing, artifacts = facet_counts(maps.values(), preps.values(), file_filter)
    selected = selected_by_study(aggregation_id)
    exportable = 0
    for sid, ids in selected.items():
        eff = effective(maps.get(int(sid), {}), file_filter)
        exportable += sum(1 for i in ids if i in eff)
    return jsonify({
        "data_types": data_types, "processing": processing, "artifacts": artifacts, "exportable": exportable,
        "selected": sum(len(ids) for ids in selected.values()),
    })


@app.route("/api/aggregations/<aggregation_id>", methods=["DELETE"])
def api_delete_aggregation(aggregation_id):
    if not delete_aggregation(aggregation_id, g.user_id):
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify({"deleted": aggregation_id})


@app.route("/api/aggregations/<aggregation_id>/studies", methods=["POST"])
def api_add_study_to_aggregation(aggregation_id):
    """Add a whole study: every one of its samples starts checked. The body's
    `study` is the Browse card's header (title, abstract, PI, year, GOLD,
    counts), snapshotted for the tab's cards."""
    study = (request.get_json() or {}).get("study")
    if not study or study.get("study_id") is None:
        return jsonify({"error": "study with study_id required"}), 400
    study_id = int(study["study_id"])
    if not is_study_public(study_id):
        return jsonify({"error": "Study is not public and cannot be added"}), 403
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    if len(agg["studies"]) >= AGGREGATION_STUDIES_CAP:
        return jsonify({"error": f"Aggregation has reached the maximum of {AGGREGATION_STUDIES_CAP} studies"}), 400
    sample_ids = list_study_sample_ids(study_id)
    # The "K / N samples" badge's denominator is the number of rows stored.
    study = {**study, "num_samples": len(sample_ids)}
    agg = add_study_to_aggregation(aggregation_id, g.user_id, study,
                                   count_fastq_artifacts(study_id), sample_ids)
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


@app.route("/api/aggregations/<aggregation_id>/studies/<int:study_id>/samples", methods=["GET"])
def api_aggregation_study_samples(aggregation_id, study_id):
    """One page of the study's samples (Qiita) flagged with `selected` (SQLite)
    and `fastq`/`fasta` availability plus each sample's file paths (`files`,
    one {artifact_id, data_type, processing, r1, r2, barcodes} per artifact) and
    its `prep_ids`. ?offset= ?limit= (1-500, default 200)
    ?q= substring filter on sample id or any metadata value ?show=
    all|with_files|without_files (default all) ?group=prep ?sort=prep|artifact
    ?dir=asc|desc (default asc).

    sort orders by each sample's lowest prep id / artifact id (artifacts that
    pass the file_filter), samples with none last either way; ties keep the
    files-first / id order. Grouped, sort=prep orders the prep groups and
    sort=artifact the rows inside each group.

    group=prep clusters by prep: a sample is listed once under every one of its
    preps whose data type passes the Data type filter (or under prep_id None,
    "No prep"), preps ascending, files-first/id order inside each. Each row
    then also carries its `prep_id`, `total` counts those (sample, prep) rows,
    and `groups` gives {prep_id, data_type, count} for every prep in the result
    (the header labels).

    The aggregation's saved file_filter first narrows which samples are in
    scope at all (in_scope: prep membership or file data type), then Show /
    with_files further narrow by file availability under the full filter.

    Paging happens in Python, not SQL: samples are sorted files-first (stable,
    so id order is preserved within each group), which a plain SQL
    LIMIT/OFFSET over qiita.sample_<id> cannot express."""
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    study = _study_in(agg, study_id)
    if study is None:
        return jsonify({"error": "Study not in aggregation"}), 404
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        # <= 500 keeps selected_in's IN (...) under SQLite's 999-bind ceiling.
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
    file_filter = agg["file_filter"]
    # Data-type scope: a sample stays in the table if it has no chosen type
    # filter, or its prep membership / file data types intersect it. This is
    # broader than `files` below — a 16S study with no per-sample file (only
    # Demultiplexed/BIOM) is still in scope, shown with FASTQ/FASTA "—".
    ids = [i for i in ids if in_scope(prep_types.get(i, []), all_files.get(i, []), file_filter)]
    files = effective(all_files, file_filter)  # {sample_id: (fastq, fasta)} under the full filter
    with_files = sum(1 for i in ids if i in files)
    if show == "with_files":
        ids = [i for i in ids if i in files]
    elif show == "without_files":
        ids = [i for i in ids if i not in files]
    # Stable sort: samples with a file first, id order preserved within each group.
    ids.sort(key=lambda i: i not in files)
    pairs, groups = _order_pairs(ids, membership, all_files, file_filter, group, sort, desc)
    total = len(pairs)
    page_pairs = pairs[offset:offset + limit]
    page = list(dict.fromkeys(i for i, _p in page_pairs))  # one sample can span several rows
    by_id = {r[0]: r for r in fetch_samples_by_ids(study_id, page)}
    columns = display_columns(study_id)
    sel = selected_in(aggregation_id, study_id, page)
    paths = page_files(study_id, page, all_files, file_filter)
    rows = []
    for sid, prep_id in page_pairs:
        r = by_id.get(sid)
        fields = dict(zip(columns, r[1:])) if r else {c: None for c in columns}
        fq, fa = files.get(sid, (0, 0))
        entries = all_files.get(sid, [])
        file_dts = sorted({e[0] for e in entries})
        row = {
            "sample_id": sid, "selected": sid in sel,
            "fastq": _FASTQ_LABEL.get(fq), "fasta": bool(fa),
            # data_types is prep membership ∪ file data types, unfiltered, so
            # the UI can show every type the sample belongs to (dimmed when
            # the filter excludes it); file_data_types is the subset with an
            # actual per-sample file, styled solid vs. outline.
            "data_types": sorted(set(prep_types.get(sid, [])) | set(file_dts)),
            "file_data_types": file_dts,
            "processing": sorted({e[1] for e in entries}),
            "files": paths[sid],
            "prep_ids": [p for p, _dt in membership.get(sid, [])],
            "fields": fields,
        }
        if group:
            row["prep_id"] = prep_id
        rows.append(row)
    resp = {
        "study_id": study_id, "total": total, "offset": offset, "limit": limit,
        "columns": columns, "selected_count": study.get("selected_samples", 0),
        "with_files": with_files, "rows": rows,
    }
    if groups is not None:
        resp["groups"] = groups
    return jsonify(resp)


def _id_list(value):
    if value is None:
        return []
    if (not isinstance(value, list) or len(value) > _MAX_IDS_PER_PATCH
            or not all(isinstance(x, str) and x for x in value)):
        raise ValueError(_SAMPLES_BODY_HELP)
    return value


@app.route("/api/aggregations/<aggregation_id>/studies/<int:study_id>/samples", methods=["PATCH"])
def api_set_aggregation_samples(aggregation_id, study_id):
    """Edit which of the study's samples are checked. Either explicit lists
    {"add": [...], "remove": [...]} or a bulk {"select": "all" | "none" |
    "with_files" | "matching", "q": ...}. "all"/"none"/"with_files" replace the
    whole selection ("with_files" = every sample with a sequence
    file — exactly what the CSV export can contain); "matching" (requires q)
    adds every sample the filter hits to the current selection.
    Ids are stored as given; one that isn't a real sample never resolves to a
    file in the CSV."""
    body = request.get_json() or {}
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    if _study_in(agg, study_id) is None:
        return jsonify({"error": "Study not in aggregation"}), 404
    try:
        select = body.get("select")
        if select is not None:
            q = (body.get("q") or "").strip()
            if select == "all":
                kwargs = {"add": list_study_sample_ids(study_id), "clear": True}
            elif select == "none":
                kwargs = {"clear": True}
            elif select == "with_files":
                ids = matching_sample_ids(study_id, q) if q else list_study_sample_ids(study_id)
                files = effective(get_sample_files(study_id), agg["file_filter"])
                kwargs = {"add": [i for i in ids if i in files], "clear": True}
            elif select == "matching" and q:
                kwargs = {"add": matching_sample_ids(study_id, q)}
            else:
                raise ValueError(_SAMPLES_BODY_HELP)
        else:
            kwargs = {"add": _id_list(body.get("add")), "remove": _id_list(body.get("remove"))}
            if not kwargs["add"] and not kwargs["remove"]:
                raise ValueError(_SAMPLES_BODY_HELP)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    agg = set_aggregation_samples(aggregation_id, g.user_id, study_id, **kwargs)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


def _export_rows(aggregation_id):
    """(rows, None) or (None, error response) for either export format."""
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return None, (jsonify({"error": "Aggregation not found"}), 404)
    selected = {sid: ids for sid, ids in selected_by_study(aggregation_id).items() if ids}
    if not selected:
        return None, (jsonify({"error": "No samples selected"}), 400)
    try:
        # Resolved at call time so tests can patch the module-level names.
        return fetch_export_rows(selected, agg["file_filter"]), None
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
    """Every checked sample's per-sample sequence files under the saved
    file_filter as csv / tsv / xlsx — one row per sample × artifact, columns
    study_id, sample_id, artifact_id, data_type, processing, R1, R2, barcodes
    (blank when absent). Samples with no resolvable file are omitted."""
    if ext not in _EXPORTS:
        return jsonify({"error": "export must be csv, tsv or xlsx"}), 404
    rows, err = _export_rows(aggregation_id)
    if err:
        return err
    serialize, mimetype = _EXPORTS[ext]
    return Response(serialize(rows), mimetype=mimetype, headers=_attachment(aggregation_id, ext))
