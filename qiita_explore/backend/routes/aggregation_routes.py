"""Sample Aggregation routes: a user's named set of studies plus one QIIME2 V2
manifest covering every per_sample_FASTQ artifact across them.

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
)
from helpers.fastq_manifest import count_fastq_artifacts, fetch_aggregate_manifest, to_tsv
from helpers.qiita_fetch import is_study_public


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
    agg = add_study_to_aggregation(aggregation_id, g.user_id, study, count_fastq_artifacts(study_id))
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


@app.route("/api/aggregations/<aggregation_id>/studies/<int:study_id>", methods=["DELETE"])
def api_remove_study_from_aggregation(aggregation_id, study_id):
    agg = remove_study_from_aggregation(aggregation_id, g.user_id, study_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    return jsonify(agg)


@app.route("/api/aggregations/<aggregation_id>/manifest", methods=["GET"])
def download_aggregation_manifest(aggregation_id):
    """QIIME2 V2 manifest (TSV) across every per_sample_FASTQ artifact of the aggregation's studies."""
    agg = get_aggregation(aggregation_id, g.user_id)
    if agg is None:
        return jsonify({"error": "Aggregation not found"}), 404
    study_ids = [s["study_id"] for s in agg["studies"]]
    if not study_ids:
        return jsonify({"error": "Aggregation has no studies"}), 400
    try:
        rows, paired = fetch_aggregate_manifest(study_ids)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return Response(
        to_tsv(rows, paired),
        mimetype="text/tab-separated-values",
        headers={"Content-Disposition": f"attachment; filename=manifest_aggregation_{aggregation_id}.tsv"},
    )
