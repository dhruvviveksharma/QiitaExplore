"""A study's detail as GET /api/studies/<id>/detail returns it — preps (with prep
metadata), artifacts, artifact graph, the first 200 samples — read through
study_detail_cache (6 h) and written back on a miss. Shared by the route and the
chat's study tools (helpers/study_tools.py); callers check is_study_public first.
"""
import json
from concurrent.futures import ThreadPoolExecutor

from store import get_study_detail_cache, upsert_study_detail_cache
from helpers.artifact_graph import fetch_artifact_graph
from helpers.study_samples import prep_membership
from helpers.qiita_fetch import (
    _fetch_prep_metadata_summary,
    _fetch_study_samples,
    _fetch_study_detail_from_qiita,
    _fetch_sample_context_text,
)


def _load(study_id):
    """(preps, artifacts, artifact_graph, cached_row, cache_hit). Raises when the
    preps can't be read from Qiita."""
    cached = get_study_detail_cache(study_id)
    # Key on the column, not the row: other writers create rows without preps,
    # and an earlier bug persisted "[]" into some (TKT-086) — a public study
    # always has a prep, so an empty list is a miss too.
    if cached and cached.get("preps_json") not in (None, "[]"):
        preps          = json.loads(cached["preps_json"])
        artifacts      = json.loads(cached.get("artifacts_json") or "[]")
        artifact_graph = json.loads(cached["artifact_graph_json"]) if cached.get("artifact_graph_json") else None
        cache_hit = True
    else:
        preps, artifacts = _fetch_study_detail_from_qiita(study_id)
        upsert_study_detail_cache(study_id, json.dumps(preps), json.dumps(artifacts))
        artifact_graph = None
        cache_hit = False

    # Re-fetch if cached graph predates the filepaths, command_params or
    # visibility feature
    if artifact_graph is not None:
        art_nodes = [n for n in artifact_graph if n.get("kind") == "artifact"]
        job_nodes = [n for n in artifact_graph if n.get("kind") == "job"]
        stale = (art_nodes and ("filepaths" not in art_nodes[0] or "visibility" not in art_nodes[0])) or \
                (job_nodes and "command_params" not in job_nodes[0])
        if stale:
            artifact_graph = None

    if artifact_graph is None:
        artifact_graph = fetch_artifact_graph(study_id)
        upsert_study_detail_cache(
            study_id, json.dumps(preps), json.dumps(artifacts),
            artifact_graph_json=json.dumps(artifact_graph),
        )

    prep_ids = [p.get("prep_template_id") for p in preps if p.get("prep_template_id") is not None]
    if prep_ids:
        if cache_hit and cached.get("prep_metadata_json"):
            id_to_meta = json.loads(cached["prep_metadata_json"])
            for prep in preps:
                pid = prep.get("prep_template_id")
                if pid is not None and str(pid) in id_to_meta:
                    prep.update(id_to_meta[str(pid)])
        else:
            with ThreadPoolExecutor(max_workers=min(len(prep_ids), 8)) as pool:
                meta_results = list(pool.map(_fetch_prep_metadata_summary, prep_ids))
            id_to_meta = dict(zip(prep_ids, meta_results))
            for prep in preps:
                pid = prep.get("prep_template_id")
                if pid is not None and pid in id_to_meta:
                    prep.update(id_to_meta[pid])
            upsert_study_detail_cache(
                study_id, None, None,
                prep_metadata_json=json.dumps({str(k): v for k, v in id_to_meta.items()}),
            )
    return preps, artifacts, artifact_graph, cached, cache_hit


def load_preps_and_graph(study_id):
    """(preps with prep metadata merged in, artifact_graph) — what the chat's
    study tools need, without the sample reads."""
    preps, _artifacts, artifact_graph, _cached, _hit = _load(study_id)
    return preps, artifact_graph


def get_study_detail(study_id):
    """The /detail response body."""
    preps, artifacts, artifact_graph, cached, cache_hit = _load(study_id)

    if cache_hit and cached.get("samples_json") and cached.get("total_samples") is not None:
        try:
            samples = json.loads(cached["samples_json"])
            total_samples = cached["total_samples"]
        except Exception:
            samples, total_samples = _fetch_study_samples(study_id, limit=200)
    else:
        samples, total_samples = _fetch_study_samples(study_id, limit=200)
        upsert_study_detail_cache(
            study_id, None, None,
            samples_json=json.dumps(samples), total_samples=total_samples,
        )

    if not (cached and cached.get("samples_context")):
        samples_ctx = _fetch_sample_context_text(study_id)
        if samples_ctx:
            upsert_study_detail_cache(
                study_id,
                json.dumps(preps),
                json.dumps(artifacts),
                samples_context=samples_ctx,
            )

    # Not part of samples_json: prep ids are read fresh, so the cache needs no migration.
    membership = prep_membership(study_id)
    for sample in samples:
        sample["prep_ids"] = [pid for pid, _dt in membership.get(sample["sample_id"], [])]

    return {
        "study_id":       study_id,
        "preps":          preps,
        "artifacts":      artifacts,
        "artifact_graph": artifact_graph,
        "samples":        samples,
        "total_samples":  total_samples,
        "cached":         cache_hit,
    }
