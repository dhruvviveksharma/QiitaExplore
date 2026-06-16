import json
import os

from flask import jsonify, request, send_file

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
from helpers.artifact_graph import fetch_artifact_graph
from helpers.biom_autopick import (autopick_artifact, autopick_reason,
                                   check_namespace_compatibility,
                                   studies_type_intersection, _namespace)
from helpers.biom_samples import get_biom_sample_ids, compute_merge_preview, build_per_study_sample_rows
from helpers.qiita_fetch import _get_or_fetch_full_samples
from helpers.merge_executor import run_merge_job, MERGE_RESULTS_DIR

_DEFAULT_USER = "default"

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


def _user_id():
    return (request.args.get("user_id") or
            (request.get_json(silent=True) or {}).get("user_id") or
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


@app.route("/api/merge-workspaces/<workspace_id>", methods=["PATCH"])
def patch_merge_workspace(workspace_id):
    user_id = _user_id()
    name = ((request.json or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    rename_workspace(workspace_id, user_id, name)
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
    chosen = body.get("chosen_artifact_ids") or body.get("chosen_artifact_id")
    studies = update_workspace_study(
        workspace_id,
        study_id,
        chosen_artifact_ids=chosen if isinstance(chosen, list) else ([chosen] if chosen else []),
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
        chosen_ids = slot.get("chosen_artifact_ids") or []
        if chosen_ids:
            chosen_arts = [a for a in artifacts if a.get("artifact_id") in set(chosen_ids)]
            artifact = chosen_arts[0] if chosen_arts else autopick_artifact(type_artifacts, common_type)
        else:
            artifact = autopick_artifact(type_artifacts, common_type)

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
            "is_chosen": bool(chosen_ids),
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
    """Return all samples (with metadata) for the union of chosen artifacts in a workspace."""
    user_id = _user_id()
    ws = get_workspace(workspace_id, user_id)
    if ws is None:
        return jsonify({"error": "Not found"}), 404

    studies_list = ws.get("studies") or []
    common_type = studies_type_intersection([s.get("data_types", "") for s in studies_list])

    entries = []
    for slot in studies_list:
        sid = int(slot["study_id"])
        artifacts = _get_artifacts(sid)
        type_artifacts = _type_filtered_artifacts(artifacts, common_type)
        chosen_ids = slot.get("chosen_artifact_ids") or []
        if chosen_ids:
            chosen_arts = [a for a in artifacts if a.get("artifact_id") in set(chosen_ids)]
            artifact = chosen_arts[0] if chosen_arts else autopick_artifact(type_artifacts, common_type)
        else:
            artifact = autopick_artifact(type_artifacts, common_type)

        if not artifact or not artifact.get("full_path"):
            continue

        # Read metadata from cache; fall back to Qiita fetch if not cached yet
        meta_by_id = {}
        try:
            cached = get_study_detail_cache(sid)
            if cached and cached.get("full_samples_json"):
                for row in json.loads(cached["full_samples_json"]):
                    meta_by_id[row["sample_id"]] = row.get("fields", {})
            else:
                samples = _get_or_fetch_full_samples(sid)
                if samples:
                    meta_by_id = {r["sample_id"]: r.get("fields", {}) for r in samples}
        except Exception:
            pass

        entries.append({
            "study_id": sid,
            "study_title": slot.get("study_title", ""),
            "artifact_id": artifact["artifact_id"],
            "full_path": artifact["full_path"],
            "meta_by_id": meta_by_id,
        })

    try:
        studies = build_per_study_sample_rows(entries)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"studies": studies})


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
    return jsonify(list_merge_jobs(workspace_id))


@app.route("/api/merge-jobs/<job_id>", methods=["GET"])
def poll_merge_job(job_id):
    job = get_merge_job(job_id)
    if job is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(job)


# ── Per-artifact sample endpoints ─────────────────────────────────────────────

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

    # Attach a few metadata fields from full_samples_json cache
    cached = get_study_detail_cache(study_id)
    meta_by_id = {}
    if cached and cached.get("full_samples_json"):
        try:
            for row in json.loads(cached["full_samples_json"]):
                meta_by_id[row["sample_id"]] = row.get("fields", {})
        except Exception:
            pass

    rows = []
    for sid in sample_ids[:limit]:
        fields = meta_by_id.get(sid, {})
        # Include up to 3 metadata columns for the peek table
        preview_cols = ["host_subject_id", "sample_type", "env_biome", "body_site"]
        rows.append({
            "sample_id": sid,
            "fields": {k: fields[k] for k in preview_cols if k in fields},
        })

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
