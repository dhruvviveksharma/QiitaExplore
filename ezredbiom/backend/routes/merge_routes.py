import json
import os

from flask import jsonify, request, send_file

from run import app, _bg_executor
from store import (
    create_workspace,
    list_workspaces,
    get_workspace,
    delete_workspace,
    add_study_to_workspace,
    remove_study_from_workspace,
    update_workspace_study,
    create_merge_job,
    get_merge_job,
    list_merge_jobs,
    update_merge_job_status,
    get_study_detail_cache,
    upsert_study_detail_cache,
)
from helpers.qiita_fetch import _fetch_study_detail_from_qiita
from helpers.biom_autopick import (autopick_artifact, check_namespace_compatibility,
                                   studies_type_intersection, _namespace)
from helpers.merge_executor import run_merge_job, MERGE_RESULTS_DIR

_DEFAULT_USER = "default"


def _user_id():
    return (request.args.get("user_id") or
            (request.json or {}).get("user_id") or
            _DEFAULT_USER)


def _get_artifacts(study_id: int) -> list:
    """Return artifact list from cache (warm) or Qiita DB (cold)."""
    cached = get_study_detail_cache(study_id)
    if cached and cached.get("artifacts_json"):
        return json.loads(cached["artifacts_json"])
    preps, artifacts = _fetch_study_detail_from_qiita(study_id)
    upsert_study_detail_cache(study_id, json.dumps(preps), json.dumps(artifacts))
    return artifacts


def _type_filtered_artifacts(artifacts: list, common_type: str) -> list:
    """Return artifacts whose namespace matches common_type, or all if none match."""
    if not common_type:
        return artifacts
    filtered = [a for a in artifacts if _namespace(a.get("data_type", "")) == common_type]
    return filtered or artifacts


def _get_sample_ids(study_id: int):
    """Return sample IDs from full_samples_json cache if available."""
    cached = get_study_detail_cache(study_id)
    if cached and cached.get("full_samples_json"):
        try:
            rows = json.loads(cached["full_samples_json"])
            return [r["sample_id"] for r in rows if r.get("sample_id")]
        except Exception:
            pass
    return None


# ── Workspace CRUD ────────────────────────────────────────────────────────────

@app.route("/api/merge-workspaces", methods=["GET"])
def list_merge_workspaces():
    user_id = _user_id()
    return jsonify(list_workspaces(user_id))


@app.route("/api/merge-workspaces", methods=["POST"])
def create_merge_workspace():
    body = request.json or {}
    name = (body.get("name") or "").strip() or "Untitled Merge"
    user_id = (body.get("user_id") or _DEFAULT_USER).strip() or _DEFAULT_USER
    ws = create_workspace(user_id, name)
    return jsonify(ws), 201


@app.route("/api/merge-workspaces/<workspace_id>", methods=["GET"])
def get_merge_workspace(workspace_id):
    user_id = _user_id()
    ws = get_workspace(workspace_id, user_id)
    if ws is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(ws)


@app.route("/api/merge-workspaces/<workspace_id>", methods=["DELETE"])
def delete_merge_workspace(workspace_id):
    user_id = _user_id()
    ok = delete_workspace(workspace_id, user_id)
    if not ok:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"deleted": workspace_id})


# ── Studies in workspace ──────────────────────────────────────────────────────

@app.route("/api/merge-workspaces/<workspace_id>/studies", methods=["POST"])
def add_study_to_merge_workspace(workspace_id):
    body = request.json or {}
    study = {
        "study_id": body.get("study_id"),
        "study_title": body.get("study_title"),
        "data_types": body.get("data_types"),
        "num_samples": body.get("num_samples"),
    }
    if not study["study_id"]:
        return jsonify({"error": "study_id required"}), 400
    studies = add_study_to_workspace(workspace_id, study)
    if studies is None:
        return jsonify({"error": "Workspace already has 5 studies (maximum)"}), 400
    return jsonify({"studies": studies}), 201


@app.route("/api/merge-workspaces/<workspace_id>/studies/<int:study_id>", methods=["DELETE"])
def remove_study_from_merge_workspace(workspace_id, study_id):
    studies = remove_study_from_workspace(workspace_id, study_id)
    return jsonify({"studies": studies})


@app.route("/api/merge-workspaces/<workspace_id>/studies/<int:study_id>", methods=["PATCH"])
def update_merge_workspace_study(workspace_id, study_id):
    body = request.json or {}
    studies = update_workspace_study(
        workspace_id,
        study_id,
        chosen_artifact_id=body.get("chosen_artifact_id"),
        sample_filter=body.get("sample_filter"),
    )
    return jsonify({"studies": studies})


# ── Validate ──────────────────────────────────────────────────────────────────

@app.route("/api/merge-workspaces/<workspace_id>/validate", methods=["GET"])
def validate_merge_workspace(workspace_id):
    user_id = _user_id()
    ws = get_workspace(workspace_id, user_id)
    if ws is None:
        return jsonify({"error": "Not found"}), 404

    studies_list = ws.get("studies") or []

    # Study-level intersection check
    common_type = studies_type_intersection([s.get("data_types", "") for s in studies_list])
    if len(studies_list) > 1 and not common_type:
        return jsonify({
            "compatible": False,
            "namespace_groups": {},
            "warnings": [],
            "errors": ["Studies share no data type in common. Select studies with at least one overlapping data type (e.g. both have 16S or both have ITS)."],
            "studies": [],
        })

    studies_payload = []
    for slot in studies_list:
        sid = int(slot["study_id"])
        artifacts = _get_artifacts(sid)

        # Pre-filter artifacts to intersection type, then autopick within that set
        type_artifacts = _type_filtered_artifacts(artifacts, common_type)
        chosen_id = slot.get("chosen_artifact_id")
        if chosen_id:
            artifact = next((a for a in artifacts if a.get("artifact_id") == chosen_id), None)
            if artifact is None:
                artifact = autopick_artifact(type_artifacts, common_type)
        else:
            artifact = autopick_artifact(type_artifacts, common_type)

        # Gather sample IDs (for collision/length checks)
        sample_filter = slot.get("sample_filter")
        if sample_filter:
            try:
                sample_ids = json.loads(sample_filter) if isinstance(sample_filter, str) else sample_filter
            except Exception:
                sample_ids = None
        else:
            sample_ids = _get_sample_ids(sid)

        studies_payload.append({
            "study_id": sid,
            "artifact": artifact,
            "sample_ids": sample_ids,
            "is_chosen": bool(chosen_id),
        })

    validation = check_namespace_compatibility(studies_payload, explicit_only=True)

    response_studies = []
    for entry in studies_payload:
        sid = entry["study_id"]
        slot = next(s for s in ws["studies"] if int(s["study_id"]) == sid)
        response_studies.append({
            "study_id": sid,
            "auto_artifact": entry["artifact"],
            "chosen_artifact_id": slot.get("chosen_artifact_id"),
        })

    return jsonify({
        "compatible": validation["compatible"],
        "namespace_groups": validation["namespace_groups"],
        "warnings": validation["warnings"],
        "errors": validation["errors"],
        "studies": response_studies,
    })


# ── Jobs ──────────────────────────────────────────────────────────────────────

@app.route("/api/merge-workspaces/<workspace_id>/jobs", methods=["POST"])
def submit_merge_job(workspace_id):
    body = request.json or {}
    user_id = (body.get("user_id") or _DEFAULT_USER).strip() or _DEFAULT_USER

    ws = get_workspace(workspace_id, user_id)
    if ws is None:
        return jsonify({"error": "Not found"}), 404
    if not ws.get("studies"):
        return jsonify({"error": "No studies in workspace"}), 400

    # Study-level intersection check
    studies_list = ws["studies"]
    common_type = studies_type_intersection([s.get("data_types", "") for s in studies_list])
    if len(studies_list) > 1 and not common_type:
        return jsonify({
            "error": "Studies share no data type in common.",
            "errors": ["Studies share no data type in common. Select studies with at least one overlapping data type."],
        }), 400

    # Validate first
    studies_for_validation = []
    workspace_snap = []
    for slot in studies_list:
        sid = int(slot["study_id"])
        artifacts = _get_artifacts(sid)
        type_artifacts = _type_filtered_artifacts(artifacts, common_type)

        chosen_id = slot.get("chosen_artifact_id")
        if chosen_id:
            artifact = next((a for a in artifacts if a.get("artifact_id") == chosen_id), None) \
                       or autopick_artifact(type_artifacts, common_type)
        else:
            artifact = autopick_artifact(type_artifacts, common_type)

        if artifact is None:
            return jsonify({"error": f"Study {sid} has no BIOM artifact"}), 400
        if not artifact.get("full_path"):
            return jsonify({"error": f"Study {sid} artifact has no file path"}), 400

        sample_filter = slot.get("sample_filter")
        sample_ids = None
        if sample_filter:
            try:
                sample_ids = json.loads(sample_filter) if isinstance(sample_filter, str) else sample_filter
            except Exception:
                pass

        studies_for_validation.append({
            "study_id": sid,
            "artifact": artifact,
            "sample_ids": sample_ids or _get_sample_ids(sid),
        })
        workspace_snap.append({
            "study_id": sid,
            "artifact_path": artifact["full_path"],
            "sample_ids": sample_ids,
        })

    validation = check_namespace_compatibility(studies_for_validation)
    if not validation["compatible"]:
        return jsonify({
            "error": "Workspace validation failed",
            "errors": validation["errors"],
        }), 400

    job = create_merge_job(workspace_id, user_id, workspace_snap)
    job_id = job["job_id"]

    def _on_status(status, error=None, result_path=None):
        update_merge_job_status(job_id, status, error_message=error, result_path=result_path)

    _bg_executor.submit(run_merge_job, job_id, workspace_snap, _on_status)

    return jsonify(job), 202


@app.route("/api/merge-workspaces/<workspace_id>/jobs", methods=["GET"])
def get_workspace_jobs(workspace_id):
    return jsonify(list_merge_jobs(workspace_id))


@app.route("/api/merge-jobs/<job_id>", methods=["GET"])
def poll_merge_job(job_id):
    job = get_merge_job(job_id)
    if job is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(job)


@app.route("/api/merge-jobs/<job_id>/download", methods=["GET"])
def download_merge_result(job_id):
    job = get_merge_job(job_id)
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
