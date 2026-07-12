"""Per-artifact sample endpoints and file downloads — split out of
routes/merge_routes.py to keep it under the 500-line cap.
"""

import json
import os

from flask import g, jsonify, request, send_file

from run import app
from store import get_merge_job, get_study_detail_cache
from helpers.artifact_graph import fetch_artifact_graph
from helpers.biom_samples import get_biom_sample_ids
from helpers.qiita_fetch import _qiita_fetch
from routes.merge_routes import _get_artifacts

_FORBIDDEN_ROOTS = ('/etc/', '/proc/', '/sys/', '/dev/', '/root/')


def _resolve_artifact_file(study_id: int, artifact_id: int, filepath_id: int):
    """Return (real_path, filename) for a file within an artifact, with safety checks."""
    cached = get_study_detail_cache(study_id)
    graph = None
    if cached and cached.get("artifact_graph_json"):
        try:
            graph = json.loads(cached["artifact_graph_json"])
        except Exception:
            pass
    if not graph:
        graph = fetch_artifact_graph(study_id)
    node = next((n for n in graph if n.get("kind") == "artifact" and n.get("artifact_id") == artifact_id), None)
    if not node:
        raise ValueError(f"Artifact {artifact_id} not in study {study_id}")
    fp_entry = next((f for f in (node.get("filepaths") or []) if f.get("filepath_id") == filepath_id), None)
    if not fp_entry:
        raise ValueError(f"File {filepath_id} not in artifact {artifact_id}")
    full_path = fp_entry.get("full_path") or ""
    if not full_path:
        raise ValueError("No path for this file")
    real = os.path.realpath(full_path)
    if not os.path.isfile(real):
        raise ValueError("File not found on disk")
    if any(real.startswith(r) for r in _FORBIDDEN_ROOTS):
        raise ValueError("Path not in allowed directory")
    return real, fp_entry.get("filename") or os.path.basename(real)


@app.route("/api/artifacts/<int:artifact_id>/samples", methods=["GET"])
def get_artifact_samples(artifact_id):
    """Return sample IDs (+ a few metadata fields) for a BIOM artifact."""
    study_id = request.args.get("study_id", type=int)
    limit = min(request.args.get("limit", 50, type=int), 500)
    if not study_id:
        return jsonify({"error": "study_id required"}), 400

    artifacts = _get_artifacts(study_id)
    art = next((a for a in artifacts if a.get("artifact_id") == artifact_id), None)
    if not art or not art.get("full_path"):
        return jsonify({"error": "Artifact not found or has no file path"}), 404

    try:
        sample_ids = get_biom_sample_ids(artifact_id, art["full_path"])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    target_ids = sample_ids[:limit]
    meta_rows = _qiita_fetch(
        f"""
        SELECT sample_id, sample_values
        FROM qiita.sample_{study_id}
        WHERE sample_id = ANY(%s)
          AND sample_id <> 'qiita_sample_column_names'
        """,
        [target_ids],
    )
    meta_by_id = {r[0]: dict(r[1]) for r in meta_rows}

    rows = [{"sample_id": sid, "fields": meta_by_id.get(sid, {})} for sid in target_ids]
    return jsonify(rows)


@app.route("/api/artifacts/sample-counts", methods=["POST"])
def get_artifact_sample_counts():
    """Return {artifact_id: num_samples} for a batch of artifact IDs from one study."""
    body = request.json or {}
    study_id = body.get("study_id")
    artifact_ids = body.get("artifact_ids") or []
    if not study_id or not artifact_ids:
        return jsonify({"error": "study_id and artifact_ids required"}), 400

    artifacts = _get_artifacts(int(study_id))
    art_by_id = {a["artifact_id"]: a for a in artifacts}

    counts = {}
    for aid in artifact_ids:
        art = art_by_id.get(aid)
        if not art or not art.get("full_path"):
            continue
        try:
            ids = get_biom_sample_ids(aid, art["full_path"])
            counts[aid] = len(ids)
        except Exception:
            pass

    return jsonify(counts)


@app.route("/api/artifacts/<int:artifact_id>/files/<int:filepath_id>/download", methods=["GET"])
def download_artifact_file(artifact_id, filepath_id):
    study_id = request.args.get("study_id", type=int)
    if not study_id:
        return jsonify({"error": "study_id required"}), 400
    try:
        real_path, filename = _resolve_artifact_file(study_id, artifact_id, filepath_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 403
    return send_file(real_path, as_attachment=True, download_name=filename)


@app.route("/api/merge-jobs/<job_id>/download", methods=["GET"])
def download_merge_result(job_id):
    job = get_merge_job(job_id, g.user_id)
    if job is None:
        return jsonify({"error": "Not found"}), 404
    if job.get("status") != "done":
        return jsonify({"error": f"Job is {job.get('status')}, not done"}), 400
    result_path = job.get("result_path")
    if not result_path or not os.path.exists(result_path):
        return jsonify({"error": "Result file not found"}), 404
    return send_file(
        result_path,
        as_attachment=True,
        download_name=f"merge_{job_id}.tar.gz",
        mimetype="application/gzip",
    )
