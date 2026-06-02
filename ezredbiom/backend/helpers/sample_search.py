"""Bounded per-study sample-metadata search for host/organism signal.

Called on every search_studies tool invocation alongside the text search.
Candidate set is always bounded (data-type filtered studies OR top-N by
sample count) so this never scans the full 50TB database.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from qiita_db.sql_connection import TRN
from services.study_service import build_data_type_filter
from helpers.qiita_fetch import _fetch_study_header

logger = logging.getLogger(__name__)

# Per-sample JSONB fields that carry host/organism identity
_HOST_FIELDS = [
    "scientific_name", "common_name",
    "host_scientific_name", "host_common_name",
    "env_feature", "taxon_id", "host_taxid",
]
_MAX_KEYWORDS_PER_PROBE = 10   # keep per-study EXISTS query small
_PROBE_TIMEOUT = 8.0            # seconds per TRN transaction


def _get_candidate_ids(data_types, exclude_ids, max_candidates):
    """Return up to max_candidates public study IDs to probe, minus exclude_ids.

    Scope priority:
    1. If data_types active → studies matching the data-type filter, ordered
       by num_samples DESC.
    2. Else → top max_candidates public studies by num_samples DESC.
    This ensures the sample-probe set is always bounded.
    """
    dt_sql, dt_params = build_data_type_filter(data_types)
    where = f"AND {dt_sql}" if dt_sql else ""
    try:
        with TRN:
            TRN.add(f"""
                SELECT DISTINCT s.study_id,
                    (SELECT COUNT(*) FROM qiita.study_sample ss
                     WHERE ss.study_id = s.study_id) AS n
                FROM qiita.study s
                LEFT JOIN qiita.study_artifact sa ON s.study_id = sa.study_id
                LEFT JOIN qiita.artifact a ON sa.artifact_id = a.artifact_id
                LEFT JOIN qiita.visibility v ON a.visibility_id = v.visibility_id
                WHERE v.visibility = 'public'
                {where}
                ORDER BY n DESC NULLS LAST
                LIMIT %s
            """, dt_params + [max_candidates * 2])
            rows = TRN.execute_fetchindex()
    except Exception:
        logger.exception("_get_candidate_ids failed")
        return []

    exclude = set(exclude_ids or [])
    result = []
    for row in rows:
        sid = row[0]
        if sid not in exclude:
            result.append(sid)
            if len(result) >= max_candidates:
                break
    return result


def _probe_study(study_id, topic_keywords):
    """Return True if any host field in sample_{study_id} matches a topic keyword.

    Uses a single EXISTS query with JSONB field extraction — no full table scan.
    """
    kws = [k.strip() for k in topic_keywords if k.strip()][:_MAX_KEYWORDS_PER_PROBE]
    if not kws:
        return False
    sid = int(study_id)
    conditions, params = [], [sid]
    for field in _HOST_FIELDS:
        for kw in kws:
            conditions.append(f"sm.sample_values->>'{field}' ILIKE %s")
            params.append(f"%{kw}%")
    try:
        with TRN:
            TRN.add(f"""
                SELECT EXISTS (
                    SELECT 1 FROM qiita.study_sample ss
                    JOIN qiita.sample_{sid} sm ON ss.sample_id = sm.sample_id
                    WHERE ss.study_id = %s
                      AND ss.sample_id <> 'qiita_sample_column_names'
                      AND ({" OR ".join(conditions)})
                )
            """, params)
            rows = TRN.execute_fetchindex()
            return bool(rows and rows[0][0])
    except Exception:
        return False


def search_studies_by_sample_meta(topic_keywords, data_types=None,
                                   exclude_ids=None, max_candidates=40):
    """Search for studies whose *sample* metadata matches topic keywords.

    Probes at most max_candidates studies in parallel (pool ≤ 8). Candidates
    are the data-type-filtered public studies (or top-N by sample count if no
    data_types filter). Returns matched studies in standard dict shape with an
    extra 'via': 'sample_metadata' tag.
    """
    kws = [k.strip() for k in (topic_keywords or []) if k.strip()]
    if not kws:
        return []

    candidate_ids = _get_candidate_ids(data_types, exclude_ids, max_candidates)
    if not candidate_ids:
        return []

    matched_ids = []
    pool_size = min(len(candidate_ids), 8)
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        futures = {pool.submit(_probe_study, sid, kws): sid for sid in candidate_ids}
        for fut in as_completed(futures, timeout=30):
            sid = futures[fut]
            try:
                if fut.result():
                    matched_ids.append(sid)
            except Exception:
                pass

    if not matched_ids:
        return []

    # Hydrate to standard dict shape using the cached header query
    studies = []
    for sid in matched_ids:
        header = _fetch_study_header(sid)
        if header:
            header["via"] = "sample_metadata"
            studies.append(header)
    return studies
