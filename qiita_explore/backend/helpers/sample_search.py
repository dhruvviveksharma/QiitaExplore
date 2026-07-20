"""Sample-metadata search for host/organism signal.

Default search: candidate set is bounded (≤40 studies, data-type filtered).
Deep search: up to max_candidates (e.g. 500) using a per-thread psycopg2
connection pool — bypasses the shared TRN singleton, which is not thread-safe.
"""

import concurrent.futures
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from psycopg2.pool import ThreadedConnectionPool
from qiita_core.qiita_settings import qiita_config
from qiita_db.sql_connection import TRN
from services.study_service import build_data_type_filter
from helpers.qiita_fetch import _fetch_study_header
from config import SAMPLE_SEARCH_PROBE_TIMEOUT_MS

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


def _probe_pool(workers):
    """A per-call ThreadedConnectionPool — parallel probes need independent
    psycopg2 connections since the shared TRN singleton is not thread-safe."""
    return ThreadedConnectionPool(
        1, workers,
        user=qiita_config.user,
        password=qiita_config.password,
        database=qiita_config.database,
        host=qiita_config.host,
        port=qiita_config.port,
        options=f"-c statement_timeout={SAMPLE_SEARCH_PROBE_TIMEOUT_MS}",
    )


def _probe_exists(pool, sid, clauses, params, tag):
    """Run a `SELECT EXISTS` probe against sample_{sid} on a pooled connection."""
    sql = f"""
        SELECT EXISTS (
            SELECT 1 FROM qiita.study_sample ss
            JOIN qiita.sample_{sid} sm ON ss.sample_id = sm.sample_id
            WHERE ss.study_id = %s
              AND ss.sample_id <> 'qiita_sample_column_names'
              AND ({" OR ".join(clauses)})
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
        logger.exception("%s study=%s failed", tag, sid)
        return False
    finally:
        pool.putconn(conn)


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
    return _probe_exists(pool, sid, conditions, params, "probe")


def _probe_fields_raw(pool, study_id, field_filters, keywords):
    """Return True if any sample in study matches field_filters or keywords.

    field_filters: list of {"field": str, "value": str} → sample_values->>'field' ILIKE '%value%'
    keywords:      free-text list → sample_values::text ILIKE '%kw%'
    Uses a dedicated connection from the pool, safe to call from multiple threads.
    """
    sid = int(study_id)
    clauses, params = [], [sid]
    for f in field_filters[:_MAX_KEYWORDS_PER_PROBE]:
        clauses.append(f"sm.sample_values->>'{f['field']}' ILIKE %s")
        params.append(f"%{f['value']}%")
    for kw in keywords[:_MAX_KEYWORDS_PER_PROBE]:
        clauses.append("sm.sample_values::text ILIKE %s")
        params.append(f"%{kw}%")
    if not clauses:
        return False
    return _probe_exists(pool, sid, clauses, params, "_probe_fields_raw")


def _parallel_probe(candidate_ids, submit, log_tag, scanned_label="scanned", pool_size=16):
    """Fan `submit(pool, sid)` out across a thread pool with a bounded timeout.

    Returns the list of candidate IDs for which `submit` returned truthy. On
    timeout, logs a partial-results warning and cancels the remaining futures.
    """
    workers = min(len(candidate_ids), pool_size)
    timeout = max(30, len(candidate_ids) * 0.4)

    pool = _probe_pool(workers)
    matched_ids = []
    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(submit, pool, sid): sid for sid in candidate_ids}
    try:
        for fut in as_completed(futures, timeout=timeout):
            sid = futures[fut]
            try:
                if fut.result():
                    matched_ids.append(sid)
            except Exception:
                pass
    except (TimeoutError, concurrent.futures.TimeoutError):
        n_done = sum(1 for f in futures if f.done())
        logger.warning(
            "[%s] timed out after %.0fs; %d/%d %s, %d matched — returning partial",
            log_tag, timeout, n_done, len(candidate_ids), scanned_label, len(matched_ids),
        )
        for f in futures:
            f.cancel()
    finally:
        # shutdown(wait=False) returns immediately; background threads finish on their own
        executor.shutdown(wait=False)
        pool.closeall()
    return matched_ids


def _hydrate_headers(ids):
    """Fetch study-header dicts for matched IDs, tagging each with its search origin."""
    studies = []
    for sid in ids:
        header = _fetch_study_header(sid)
        if header:
            header["via"] = "sample_metadata"
            studies.append(header)
    return studies


def search_studies_by_field_filters(field_filters=None, keywords=None,
                                     data_types=None, exclude_ids=None,
                                     max_candidates=200, pool_size=16):
    """Search studies whose sample metadata matches structured field filters or keywords.

    field_filters: [{"field": str, "value": str}] — checked via JSONB key lookup
    keywords:      [str] — checked across full JSONB text (any field)
    Returns matched studies in standard header dict shape with 'via': 'sample_metadata'.
    """
    ff  = [f for f in (field_filters or []) if f.get("field") and f.get("value")]
    kws = [k.strip() for k in (keywords or []) if k.strip()]
    if not ff and not kws:
        return []

    candidate_ids = _get_candidate_ids(data_types, exclude_ids, max_candidates)
    if not candidate_ids:
        return []

    matched_ids = _parallel_probe(
        candidate_ids,
        lambda pool, sid: _probe_fields_raw(pool, sid, ff, kws),
        "field_filter_search",
        pool_size=pool_size,
    )
    return _hydrate_headers(matched_ids)


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

    matched_ids = _parallel_probe(
        candidate_ids,
        lambda pool, sid: _probe_study_raw(pool, sid, kws),
        "sample_search",
        scanned_label="studies scanned",
        pool_size=pool_size,
    )
    return _hydrate_headers(matched_ids)
