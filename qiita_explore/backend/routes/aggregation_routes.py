"""Sample Aggregation routes: a user's named set of samples, grouped by study,
plus one CSV of the checked samples' per-sample sequence files
(study_id, sample_id, file_path_in_qmounts, data_type, file_type).

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
    delete_aggregation,
    add_study_to_aggregation,
    remove_study_from_aggregation,
    set_aggregation_samples,
    selected_in,
    selected_by_study,
)
from helpers.fastq_manifest import count_fastq_artifacts, fetch_aggregate_csv_rows, get_sample_files, to_csv
from helpers.qiita_fetch import is_study_public
from helpers.study_samples import (
    display_columns, fetch_samples_by_ids, list_study_sample_ids, matching_sample_ids,
)

_MAX_IDS_PER_PATCH = 50_000
_SAMPLES_BODY_HELP = ('body must be {"add": [...], "remove": [...]} or '
                      '{"select": "all" | "none" | "with_files" | "matching", "q": "..."}')
_SHOW_VALUES = ("all", "with_files", "without_files")
# {fastq availability int -> API string}; 0 (none) -> None, handled separately.
_FASTQ_LABEL = {2: "paired", 1: "single"}


@app.route("/api/aggregations", methods=["GET"])
def api_list_aggregations():
    return jsonify({"aggregations": list_aggregations(g.user_id)})


@app.route("/api/aggregations", methods=["POST"])
def api_create_aggregation():
    name = ((request.get_json() or {}).get("name") or "").strip() or "Untitled"
    return jsonify(create_aggregation(g.user_id, name)), 201


@app.route("/api/aggregations/<aggregation_id>", methods=["PATCH"])
def api_rename_aggregation(aggregation_id):
    name = ((request.get_json() or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    agg = rename_aggregation(aggregation_id, g.user_id, name)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


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
    and `fastq`/`fasta` availability. ?offset= ?limit= (1-500, default 200)
    ?q= substring filter on sample id or any metadata value ?show=
    all|with_files|without_files (default all).

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
    q = (request.args.get("q") or "").strip() or None

    ids = matching_sample_ids(study_id, q) if q else list_study_sample_ids(study_id)
    files = get_sample_files(study_id)
    with_files = sum(1 for i in ids if i in files)
    if show == "with_files":
        ids = [i for i in ids if i in files]
    elif show == "without_files":
        ids = [i for i in ids if i not in files]
    total = len(ids)
    # Stable sort: samples with a file first, id order preserved within each group.
    ids.sort(key=lambda i: i not in files)

    page = ids[offset:offset + limit]
    by_id = {r[0]: r for r in fetch_samples_by_ids(study_id, page)}
    columns = display_columns(study_id)
    sel = selected_in(aggregation_id, study_id, page)
    rows = []
    for sid in page:
        r = by_id.get(sid)
        fields = dict(zip(columns, r[1:])) if r else {c: None for c in columns}
        fq, fa = files.get(sid, (0, 0))
        rows.append({
            "sample_id": sid, "selected": sid in sel,
            "fastq": _FASTQ_LABEL.get(fq), "fasta": bool(fa),
            "fields": fields,
        })
    return jsonify({
        "study_id": study_id, "total": total, "offset": offset, "limit": limit,
        "columns": columns, "selected_count": study.get("selected_samples", 0),
        "with_files": with_files, "rows": rows,
    })


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
    whole selection ("with_files" = every sample with a per-sample sequence
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
                files = get_sample_files(study_id)
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


@app.route("/api/aggregations/<aggregation_id>/export.csv", methods=["GET"])
def download_aggregation_csv(aggregation_id):
    """CSV of every checked sample's per-sample sequence files — one row per
    file (a paired sample gives two), columns study_id, sample_id,
    file_path_in_qmounts, data_type, file_type. Samples with no resolvable
    file are omitted. ?spreadsheet=1 wraps sample_id as ="..." so Excel /
    Numbers keep an id like 10317.000001062 as text instead of parsing (and
    rounding) it as a number, which otherwise displays indistinguishably from
    the study_id column."""
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    selected = {sid: ids for sid, ids in selected_by_study(aggregation_id).items() if ids}
    if not selected:
        return jsonify({"error": "No samples selected"}), 400
    try:
        rows = fetch_aggregate_csv_rows(selected)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    spreadsheet = request.args.get("spreadsheet") in ("1", "true", "yes")
    suffix = "_spreadsheet" if spreadsheet else ""
    return Response(
        to_csv(rows, spreadsheet_safe=spreadsheet),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=aggregation_{aggregation_id}_samples{suffix}.csv"},
    )
