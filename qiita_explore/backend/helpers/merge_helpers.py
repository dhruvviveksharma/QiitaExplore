"""Artifact/sample resolution helpers shared by routes/merge_routes.py and
routes/artifact_routes.py — pure (no Flask dependency)."""

import json

from store import get_study_detail_cache, upsert_study_detail_cache
from helpers.qiita_fetch import _fetch_study_detail_from_qiita
from helpers.biom_autopick import autopick_artifact, _namespace


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
