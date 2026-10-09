"""Workspace (project) study enrichment, shared by POST /api/projects/<id>/studies
(routes/project_routes.py) and the chat's workspace tools (helpers/workspace_tools.py)."""
import json

from store import get_study_detail_cache, update_project_study_data, upsert_study_detail_cache
from helpers.qiita_fetch import _fetch_sample_context_text, _fetch_study_detail_from_qiita, _qiita_fetch


def enrich_study_in_project(project_id: str, study_id: int):
    """Fetch num_samples + prep detail from Qiita and update project_studies.
    The add route runs it in the background; the chat's add_to_workspace runs
    it inline, so its card shows complete counts."""
    cnt        = _qiita_fetch(
        "SELECT COUNT(*) FROM qiita.study_sample WHERE study_id = %s",
        [int(study_id)],
    )
    num_samples = cnt[0][0] if cnt else None

    preps = []
    try:
        cached = get_study_detail_cache(study_id)
        # Column, not row: see api_study_detail (TKT-086).
        if cached and cached.get("preps_json") not in (None, "[]"):
            preps = json.loads(cached["preps_json"])
        else:
            preps, artifacts = _fetch_study_detail_from_qiita(study_id)
            upsert_study_detail_cache(study_id, json.dumps(preps), json.dumps(artifacts))
    except Exception:
        pass

    data_types = None
    num_preps  = None
    preps_json = None
    if preps:
        types      = sorted({p.get("data_type") for p in preps if p.get("data_type")})
        data_types = ", ".join(types) or None
        num_preps  = len(preps)
        preps_json = json.dumps(preps)

    update_project_study_data(
        project_id,
        study_id,
        data_types=data_types,
        num_samples=num_samples,
        num_preps=num_preps,
        preps_json=preps_json,
    )

    try:
        cached_detail = get_study_detail_cache(study_id)
        if not (cached_detail and cached_detail.get("samples_context")):
            samples_ctx = _fetch_sample_context_text(study_id)
            if samples_ctx:
                upsert_study_detail_cache(study_id, None, None, samples_context=samples_ctx)
    except Exception:
        pass
