"""Artifact/sample resolution helpers shared by routes/merge_routes.py and
routes/artifact_routes.py — pure (no Flask dependency)."""

import json

from store import get_study_detail_cache, upsert_study_detail_cache
from helpers.artifact_graph import fetch_artifact_graph
from helpers.qiita_fetch import _fetch_study_detail_from_qiita, public_artifact_ids
from helpers.biom_autopick import autopick_artifact, _namespace


def public_artifacts(study_id: int) -> dict:
    """{artifact_id: graph node} for the study's public artifacts — {} when the
    study isn't public (TKT-043). Visibility is per artifact (a public study can
    still hold private or sandboxed ones) and is read live; the cached graph
    only supplies the file lists."""
    public = public_artifact_ids(study_id)
    if not public:
        return {}
    cached = get_study_detail_cache(study_id)
    graph = None
    if cached and cached.get("artifact_graph_json"):
        try:
            graph = json.loads(cached["artifact_graph_json"])
        except ValueError:
            graph = None
    nodes = [n for n in graph or [] if n.get("kind") == "artifact"]
    if not nodes or "filepaths" not in nodes[0] or "paths_v" not in nodes[0]:   # study_detail's staleness rule
        nodes = [n for n in fetch_artifact_graph(study_id) if n.get("kind") == "artifact"]
    return {n.get("artifact_id"): n for n in nodes if n.get("artifact_id") in public}


def _get_artifacts(study_id: int) -> list:
    """The study's public artifacts, from cache (warm) or Qiita DB (cold).
    Everything that reads BIOM files or sample lists for a merge workspace or
    an artifact goes through here, and every signed-in Qiita user reaches those
    routes — so private studies and non-public artifacts are left out (TKT-043)."""
    public = public_artifact_ids(study_id)
    if not public:
        return []
    cached = get_study_detail_cache(study_id)
    if cached and cached.get("artifacts_json"):
        artifacts = json.loads(cached["artifacts_json"])
    else:
        preps, artifacts = _fetch_study_detail_from_qiita(study_id)
        upsert_study_detail_cache(study_id, json.dumps(preps), json.dumps(artifacts))
    return [a for a in artifacts if a.get("artifact_id") in public]


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
