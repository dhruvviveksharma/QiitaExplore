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
from helpers.fastq_manifest import count_fastq_artifacts, fetch_aggregate_csv_rows, to_csv
from helpers.qiita_fetch import is_study_public
from helpers.study_samples import fetch_sample_page, list_study_sample_ids, matching_sample_ids

_MAX_IDS_PER_PATCH = 50_000
_SAMPLES_BODY_HELP = ('body must be {"add": [...], "remove": [...]} or '
                      '{"select": "all" | "none" | "matching", "q": "..."}')


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
    """One page of the study's samples (Qiita) flagged with `selected` (SQLite).
    ?offset= ?limit= (1-500, default 200) ?q= substring filter on sample id or
    any metadata value."""
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
    q = (request.args.get("q") or "").strip() or None
    rows, total, columns = fetch_sample_page(study_id, offset, limit, q)
    sel = selected_in(aggregation_id, study_id, [r[0] for r in rows])
    return jsonify({
        "study_id": study_id, "total": total, "offset": offset, "limit": limit,
        "columns": columns, "selected_count": study.get("selected_samples", 0),
        "rows": [{"sample_id": r[0], "selected": r[0] in sel, "fields": dict(zip(columns, r[1:]))}
                 for r in rows],
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
    "matching", "q": ...} ("matching" = every sample the filter q hits).
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
    file are omitted."""
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
    return Response(
        to_csv(rows),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=aggregation_{aggregation_id}_samples.csv"},
    )
