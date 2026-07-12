import json

from flask import g, jsonify, request

from run import app, _bg_executor
from store import (
    create_workspace,
    list_workspaces,
    get_workspace,
    delete_workspace,
    rename_workspace,
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
from helpers.biom_autopick import (autopick_artifact, autopick_reason,
                                   check_namespace_compatibility,
                                   studies_type_intersection, _namespace)
from helpers.biom_samples import get_biom_sample_ids, compute_merge_preview
from helpers.merge_samples import build_sample_page
from helpers.merge_executor import run_merge_job, MERGE_RESULTS_DIR


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


def _resolve_artifact(slot: dict, artifacts: list, common_type: str):
    """Return the chosen or autopicked BIOM artifact for a workspace study slot."""
    type_artifacts = _type_filtered_artifacts(artifacts, common_type)
    chosen_ids = slot.get("chosen_artifact_ids") or []
    if chosen_ids:
        chosen = [a for a in artifacts if a.get("artifact_id") in set(chosen_ids)]
        return chosen[0] if chosen else autopick_artifact(type_artifacts, common_type)
    return autopick_artifact(type_artifacts, common_type)


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
    return jsonify(list_workspaces(g.user_id))


@app.route("/api/merge-workspaces", methods=["POST"])
def create_merge_workspace():
    body = request.json or {}
    name = (body.get("name") or "").strip() or "Untitled Merge"
    ws = create_workspace(g.user_id, name)
    return jsonify(ws), 201


@app.route("/api/merge-workspaces/<workspace_id>", methods=["GET"])
def get_merge_workspace(workspace_id):
    ws = get_workspace(workspace_id, g.user_id)
    if ws is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(ws)


@app.route("/api/merge-workspaces/<workspace_id>", methods=["DELETE"])
def delete_merge_workspace(workspace_id):
    ok = delete_workspace(workspace_id, g.user_id)
    if not ok:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"deleted": workspace_id})


@app.route("/api/merge-workspaces/<workspace_id>", methods=["PATCH"])
def patch_merge_workspace(workspace_id):
    name = ((request.json or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    rename_workspace(workspace_id, g.user_id, name)
    return jsonify({"workspace_id": workspace_id, "name": name})


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
    studies = add_study_to_workspace(workspace_id, g.user_id, study)
    if studies == "not_found":
        return jsonify({"error": "Not found"}), 404
    if studies is None:
        return jsonify({"error": "Workspace already has 5 studies (maximum)"}), 400
    return jsonify({"studies": studies}), 201


@app.route("/api/merge-workspaces/<workspace_id>/studies/<int:study_id>", methods=["DELETE"])
def remove_study_from_merge_workspace(workspace_id, study_id):
    studies = remove_study_from_workspace(workspace_id, g.user_id, study_id)
    if studies == "not_found":
        return jsonify({"error": "Not found"}), 404
    return jsonify({"studies": studies})


@app.route("/api/merge-workspaces/<workspace_id>/studies/<int:study_id>", methods=["PATCH"])
def update_merge_workspace_study(workspace_id, study_id):
    body = request.json or {}
    chosen = body.get("chosen_artifact_ids") or body.get("chosen_artifact_id")
    studies = update_workspace_study(
        workspace_id,
        g.user_id,
        study_id,
        chosen_artifact_ids=chosen if isinstance(chosen, list) else ([chosen] if chosen else []),
        sample_filter=body.get("sample_filter"),
    )
    if studies == "not_found":
        return jsonify({"error": "Not found"}), 404
    return jsonify({"studies": studies})


# ── Validate ──────────────────────────────────────────────────────────────────

@app.route("/api/merge-workspaces/<workspace_id>/validate", methods=["GET"])
def validate_merge_workspace(workspace_id):
    ws = get_workspace(workspace_id, g.user_id)
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
        artifact = _resolve_artifact(slot, artifacts, common_type)

        # Get per-BIOM sample IDs (true membership, cached forever)
        biom_sample_ids = None
        if artifact and artifact.get("artifact_id") and artifact.get("full_path"):
            try:
                biom_sample_ids = get_biom_sample_ids(
                    artifact["artifact_id"], artifact["full_path"]
                )
            except Exception:
                biom_sample_ids = _get_sample_ids(sid)

        # Attach display fields to a copy of the artifact dict
        if artifact:
            artifact = dict(artifact)
            artifact["num_samples"] = len(biom_sample_ids) if biom_sample_ids is not None else None
            artifact["reason"] = autopick_reason(artifact, common_type)

        # Honour explicit sample filter; otherwise use per-BIOM membership
        sample_filter = slot.get("sample_filter")
        if sample_filter:
            try:
                sample_ids = json.loads(sample_filter) if isinstance(sample_filter, str) else sample_filter
            except Exception:
                sample_ids = biom_sample_ids
        else:
            sample_ids = biom_sample_ids or _get_sample_ids(sid)

        studies_payload.append({
            "study_id": sid,
            "artifact": artifact,
            "sample_ids": sample_ids,
            "is_chosen": bool(slot.get("chosen_artifact_ids")),
        })

    validation = check_namespace_compatibility(studies_payload, explicit_only=True)

    # Merge preview (only when all chosen studies have sample data)
    preview_sets = {
        e["study_id"]: e["sample_ids"]
        for e in studies_payload
        if e.get("sample_ids")
    }
    preview = compute_merge_preview(preview_sets) if len(preview_sets) >= 2 else None

    response_studies = []
    for entry in studies_payload:
        sid = entry["study_id"]
        slot = next(s for s in ws["studies"] if int(s["study_id"]) == sid)
        response_studies.append({
            "study_id": sid,
            "auto_artifact": entry["artifact"],
            "chosen_artifact_ids": slot.get("chosen_artifact_ids") or [],
        })

    return jsonify({
        "compatible": validation["compatible"],
        "namespace_groups": validation["namespace_groups"],
        "warnings": validation["warnings"],
        "errors": validation["errors"],
        "studies": response_studies,
        "preview": preview,
    })


@app.route("/api/merge-workspaces/<workspace_id>/samples", methods=["GET"])
def get_workspace_samples(workspace_id):
    """Return skeleton (study_id, study_title, total BIOM sample count) for each study."""
    ws = get_workspace(workspace_id, g.user_id)
    if ws is None:
        return jsonify({"error": "Not found"}), 404

    studies_list = ws.get("studies") or []
    common_type = studies_type_intersection([s.get("data_types", "") for s in studies_list])

    studies = []
    for slot in studies_list:
        sid = int(slot["study_id"])
        artifact = _resolve_artifact(slot, _get_artifacts(sid), common_type)
        total = 0
        if artifact and artifact.get("artifact_id") and artifact.get("full_path"):
            try:
                total = len(get_biom_sample_ids(artifact["artifact_id"], artifact["full_path"]))
            except Exception:
                pass
        studies.append({"study_id": sid, "study_title": slot.get("study_title", ""), "total": total})
    return jsonify({"studies": studies})


@app.route("/api/merge-workspaces/<workspace_id>/studies/<int:study_id>/samples", methods=["GET"])
def get_workspace_study_samples(workspace_id, study_id):
    """Return a page of samples with metadata for one study in a workspace."""
    ws = get_workspace(workspace_id, g.user_id)
    if ws is None:
        return jsonify({"error": "Not found"}), 404

    try:
        offset = max(0, int(request.args.get("offset", 0)))
        limit = min(500, max(1, int(request.args.get("limit", 100))))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid offset or limit"}), 400

    slot = next((s for s in (ws.get("studies") or []) if int(s["study_id"]) == study_id), None)
    if slot is None:
        return jsonify({"error": "Study not in workspace"}), 404

    common_type = studies_type_intersection([s.get("data_types", "") for s in ws.get("studies") or []])
    artifact = _resolve_artifact(slot, _get_artifacts(study_id), common_type)
    if not artifact or not artifact.get("full_path"):
        return jsonify({"error": "No BIOM artifact for this study"}), 404

    return jsonify(build_sample_page(
        artifact["artifact_id"], artifact["full_path"],
        study_id, slot.get("study_title", ""),
        offset, limit,
    ))


# ── Jobs ──────────────────────────────────────────────────────────────────────

@app.route("/api/merge-workspaces/<workspace_id>/jobs", methods=["POST"])
def submit_merge_job(workspace_id):
    body = request.json or {}
    user_id = g.user_id

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

        chosen_ids = slot.get("chosen_artifact_ids") or []
        if chosen_ids:
            chosen_arts = [a for a in artifacts if a.get("artifact_id") in set(chosen_ids)]
            if not chosen_arts:
                chosen_arts = [autopick_artifact(type_artifacts, common_type)]
        else:
            chosen_arts = [autopick_artifact(type_artifacts, common_type)]

        chosen_arts = [a for a in chosen_arts if a]
        if not chosen_arts:
            return jsonify({"error": f"Study {sid} has no BIOM artifact"}), 400
        for art in chosen_arts:
            if not art.get("full_path"):
                return jsonify({"error": f"Study {sid} artifact {art.get('artifact_id')} has no file path"}), 400

        sample_filter = slot.get("sample_filter")
        sample_ids = None
        if sample_filter:
            try:
                sample_ids = json.loads(sample_filter) if isinstance(sample_filter, str) else sample_filter
            except Exception:
                pass
        resolved_sample_ids = sample_ids or _get_sample_ids(sid)

        # Use first chosen artifact for namespace compatibility check
        studies_for_validation.append({
            "study_id": sid,
            "artifact": chosen_arts[0],
            "sample_ids": resolved_sample_ids,
        })
        for art in chosen_arts:
            workspace_snap.append({
                "study_id": sid,
                "artifact_id": art["artifact_id"],
                "artifact_path": art["full_path"],
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
    return jsonify(list_merge_jobs(workspace_id, g.user_id))


@app.route("/api/merge-jobs/<job_id>", methods=["GET"])
def poll_merge_job(job_id):
    job = get_merge_job(job_id, g.user_id)
    if job is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(job)


# Per-artifact sample endpoints and file downloads
# (get_artifact_samples, get_artifact_sample_counts, download_artifact_file,
# download_merge_result) moved to routes/artifact_routes.py to keep this file
# under the 500-line cap.
