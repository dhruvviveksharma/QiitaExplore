"""Paginated per-study sample rows with metadata fetched by exact BIOM ID list."""

import logging

logger = logging.getLogger(__name__)


def fetch_metadata_for_ids(study_id: int, sample_ids: list) -> dict:
    """Return {sample_id: fields} for the given IDs from qiita.sample_{study_id}."""
    from helpers.qiita_fetch import _qiita_fetch
    if not sample_ids:
        return {}
    rows = _qiita_fetch(
        f"SELECT sample_id, sample_values FROM qiita.sample_{int(study_id)} WHERE sample_id = ANY(%s)",
        [sample_ids],
    )
    return {r[0]: dict(r[1]) for r in rows} if rows else {}


def build_sample_page(artifact_id: int, full_path: str, study_id: int,
                      study_title: str, offset: int, limit: int) -> dict:
    """Return a page of sample rows with metadata.

    Returns {study_id, study_title, total, offset, limit, rows, meta_error}
    where rows = [{sample_id, fields}].
    """
    from helpers.biom_samples import get_biom_sample_ids
    meta_error = ""
    try:
        all_ids = get_biom_sample_ids(artifact_id, full_path)
    except Exception as exc:
        logger.warning("BIOM read failed for artifact %s: %s", artifact_id, exc)
        return {
            "study_id": study_id,
            "study_title": study_title,
            "total": 0,
            "offset": offset,
            "limit": limit,
            "rows": [],
            "meta_error": str(exc),
        }

    total = len(all_ids)
    page_ids = all_ids[offset: offset + limit]

    fields_by_id = {}
    try:
        fields_by_id = fetch_metadata_for_ids(study_id, page_ids)
    except Exception as exc:
        logger.warning("Metadata fetch failed for study %s: %s", study_id, exc)
        meta_error = str(exc)

    return {
        "study_id": study_id,
        "study_title": study_title,
        "total": total,
        "offset": offset,
        "limit": limit,
        "rows": [{"sample_id": sid, "fields": fields_by_id.get(sid, {})} for sid in page_ids],
        "meta_error": meta_error,
    }
