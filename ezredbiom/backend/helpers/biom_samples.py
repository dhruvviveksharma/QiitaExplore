"""Per-BIOM sample membership — read from HDF5 and cache forever (artifacts are immutable)."""

import json
import logging
import os

logger = logging.getLogger(__name__)

_QIITA_BASE = os.getenv("QIITA_BASE_DATA_DIR", "").rstrip("/")


def _resolve_path(path: str) -> str:
    if not path or os.path.isabs(path):
        return path
    return f"{_QIITA_BASE}/{path}" if _QIITA_BASE else path


def read_biom_sample_ids(path: str) -> list:
    """Return sample IDs from a BIOM file. Uses h5py direct read; falls back to biom.load_table."""
    resolved = _resolve_path(path)
    try:
        import h5py
        with h5py.File(resolved, "r") as f:
            return [s.decode() if isinstance(s, bytes) else s for s in f["sample/ids"][:]]
    except Exception:
        from biom import load_table
        return list(load_table(resolved).ids(axis="sample"))


def get_biom_sample_ids(artifact_id: int, biom_path: str) -> list:
    """Cache-through: return sample IDs from the per-BIOM cache or read from the file."""
    from store.cache import get_biom_sample_cache, upsert_biom_sample_cache
    cached = get_biom_sample_cache(artifact_id)
    if cached and cached.get("sample_ids_json"):
        try:
            return json.loads(cached["sample_ids_json"])
        except Exception:
            pass
    ids = read_biom_sample_ids(biom_path)
    upsert_biom_sample_cache(artifact_id, len(ids), json.dumps(ids))
    return ids


def build_merged_sample_rows(studies_entries: list) -> list:
    """Return [{sample_id, study_id, fields}] for the union of all studies.

    studies_entries: [{study_id, artifact_id, full_path, meta_by_id}]
    When sample IDs overlap across studies the first study in the list wins.
    """
    seen: set = set()
    rows: list = []
    for entry in studies_entries:
        try:
            ids = get_biom_sample_ids(entry["artifact_id"], entry["full_path"])
        except Exception:
            ids = []
        meta = entry.get("meta_by_id") or {}
        for sid in ids:
            if sid in seen:
                continue
            seen.add(sid)
            rows.append({"sample_id": sid, "study_id": entry["study_id"], "fields": meta.get(sid, {})})
    return rows


def compute_merge_preview(per_study_sets: dict) -> dict:
    """Return merge output statistics from per-study sample-id sets.

    per_study_sets: {study_id: list_or_set_of_sample_ids}
    Returns: {total_unique, per_study: [{study_id, num_samples}], overlap_count}
    """
    union: set = set()
    total = 0
    per_study = []
    for sid, ids in per_study_sets.items():
        s = set(ids)
        per_study.append({"study_id": int(sid), "num_samples": len(s)})
        union |= s
        total += len(s)
    return {
        "total_unique": len(union),
        "per_study": per_study,
        "overlap_count": total - len(union),
    }
