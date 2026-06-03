"""Sample-metadata search for host/organism signal.

Default search: candidate set is bounded (≤40 studies, data-type filtered).
Deep search: up to max_candidates (e.g. 500) using a per-thread psycopg2
connection pool — bypasses the shared TRN singleton, which is not thread-safe.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from qiita_core.qiita_settings import qiita_config
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
_MAX_KEYWORDS_PER_PROBE = 10


def _get_candidate_ids(data_types, exclude_ids, max_candidates):
    """Return up to max_candidates public study IDs to probe, minus exclude_ids."""
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


def _probe_study_raw(pool, study_id, kws):
    """Return True if any host field in sample_{study_id} matches a keyword.

    Uses a dedicated psycopg2 connection from the pool — safe to call from
    multiple threads simultaneously (unlike the shared TRN singleton).
    """
    kws = [k.strip() for k in kws if k.strip()][:_MAX_KEYWORDS_PER_PROBE]
    if not kws:
        return False
    sid = int(study_id)
    conditions, params = [], [sid]
    for field in _HOST_FIELDS:
        for kw in kws:
            conditions.append(f"sm.sample_values->>'{field}' ILIKE %s")
            params.append(f"%{kw}%")
    sql = f"""
        SELECT EXISTS (
            SELECT 1 FROM qiita.study_sample ss
            JOIN qiita.sample_{sid} sm ON ss.sample_id = sm.sample_id
            WHERE ss.study_id = %s
              AND ss.sample_id <> 'qiita_sample_column_names'
              AND ({" OR ".join(conditions)})
        )
    """
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return bool(row and row[0])
    except Exception:
        logger.exception("probe study=%s failed", sid)
        return False
    finally:
        pool.putconn(conn)


def search_studies_by_sample_meta(topic_keywords, data_types=None,
                                   exclude_ids=None, max_candidates=500,
                                   pool_size=16):
    """Search for studies whose sample metadata matches topic keywords.

    Uses a per-call ThreadedConnectionPool so parallel probes run on independent
    psycopg2 connections (the shared TRN singleton is not thread-safe).
    Returns matched studies in standard dict shape with 'via': 'sample_metadata'.
    """
    kws = [k.strip() for k in (topic_keywords or []) if k.strip()]
    if not kws:
        return []

    candidate_ids = _get_candidate_ids(data_types, exclude_ids, max_candidates)
    if not candidate_ids:
        return []

    workers = min(len(candidate_ids), pool_size)
    timeout = max(30, len(candidate_ids) * 0.4)  # ~0.4s budget per study

    pool = ThreadedConnectionPool(
        1, workers,
        user=qiita_config.user,
        password=qiita_config.password,
        database=qiita_config.database,
        host=qiita_config.host,
        port=qiita_config.port,
    )
    matched_ids = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_probe_study_raw, pool, sid, kws): sid
                for sid in candidate_ids
            }
            for fut in as_completed(futures, timeout=timeout):
                sid = futures[fut]
                try:
                    if fut.result():
                        matched_ids.append(sid)
                except Exception:
                    pass
    finally:
        pool.closeall()

    if not matched_ids:
        return []

    studies = []
    for sid in matched_ids:
        header = _fetch_study_header(sid)
        if header:
            header["via"] = "sample_metadata"
            studies.append(header)
    return studies
