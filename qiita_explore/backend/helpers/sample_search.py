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
from services.relevance import build_pi_required_filter
from helpers.qiita_fetch import _fetch_study_headers, _PUBLIC_ARTIFACT_EXISTS
from config import SAMPLE_SEARCH_PROBE_TIMEOUT_MS

logger = logging.getLogger(__name__)

_MAX_KEYWORDS_PER_PROBE = 10


def _get_candidate_ids(data_types, exclude_ids, max_candidates, resolved_pis=None):
    """Return up to max_candidates public study IDs to probe, minus exclude_ids."""
    dt_sql, dt_params = build_data_type_filter(data_types)
    pi_sql, pi_params = build_pi_required_filter(resolved_pis or [])
    joins = ""
    if pi_sql:
        joins = (
            " LEFT JOIN qiita.study_person sp_pi"
            " ON s.principal_investigator_id = sp_pi.study_person_id"
        )
    where_parts = []
    if dt_sql:
        where_parts.append(dt_sql)
    if pi_sql:
        where_parts.append(pi_sql)
    extra_where = (" AND " + " AND ".join(where_parts)) if where_parts else ""
    query_params = list(dt_params) + list(pi_params) + [max_candidates * 2]
    try:
        with TRN:
            TRN.add(f"""
                SELECT s.study_id,
                    (SELECT COUNT(*) FROM qiita.study_sample ss
                     WHERE ss.study_id = s.study_id) AS n
                FROM qiita.study s
                {joins}
                WHERE {_PUBLIC_ARTIFACT_EXISTS}
                {extra_where}
                ORDER BY n DESC NULLS LAST
                LIMIT %s
            """, query_params)
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
    """Return True if any sample metadata JSONB text matches a keyword."""
    kws = [k.strip() for k in kws if k.strip()]
    if not kws:
        return False
    # Direct user terms come first in the expanded list (see
    # study_service.expand_keyword_variants' tiering), so this cap sheds
    # synonym padding under the probe timeout, not user terms.
    kws = kws[:_MAX_KEYWORDS_PER_PROBE]
    sid = int(study_id)
    sql = f"""
        SELECT EXISTS (
            SELECT 1 FROM unnest(%s::text[]) AS kw
            WHERE EXISTS (
                SELECT 1 FROM qiita.study_sample ss
                JOIN qiita.sample_{sid} sm ON ss.sample_id = sm.sample_id
                WHERE ss.study_id = %s
                  AND ss.sample_id <> 'qiita_sample_column_names'
                  AND sm.sample_values::text ILIKE ('%%' || kw || '%%')
            )
        )
    """
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql, [kws, sid])
            row = cur.fetchone()
            return bool(row and row[0])
    except Exception:
        logger.exception("probe study=%s failed", sid)
        return False
    finally:
        pool.putconn(conn)


def _score_sample_metadata_raw(pool, study_id, keywords):
    """Return count of keywords with at least one sample metadata match."""
    kws = [k.strip() for k in (keywords or []) if k.strip()]
    if not kws:
        return 0
    # See _probe_study_raw — direct user terms lead the expanded list, so
    # this cap sheds synonym padding, not user terms.
    kws = kws[:_MAX_KEYWORDS_PER_PROBE]
    sid = int(study_id)
    sql = f"""
        SELECT COUNT(DISTINCT kw)
        FROM unnest(%s::text[]) AS kw
        WHERE EXISTS (
            SELECT 1 FROM qiita.study_sample ss
            JOIN qiita.sample_{sid} sm ON ss.sample_id = sm.sample_id
            WHERE ss.study_id = %s
              AND ss.sample_id <> 'qiita_sample_column_names'
              AND sm.sample_values::text ILIKE ('%%' || kw || '%%')
        )
    """
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql, [kws, sid])
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        logger.exception("score_sample_metadata study=%s failed", sid)
        return 0
    finally:
        pool.putconn(conn)


def score_studies_sample_layer(study_ids, keywords, pool_size=16):
    """Score sample-metadata keyword hits for each study ID in parallel."""
    ids = [int(s) for s in (study_ids or [])]
    if not ids:
        return {}
    workers = min(len(ids), pool_size)
    timeout = max(30, len(ids) * 0.4)
    pool = _probe_pool(workers)
    scores = {}
    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(_score_sample_metadata_raw, pool, sid, keywords): sid for sid in ids}
    try:
        for fut in as_completed(futures, timeout=timeout):
            sid = futures[fut]
            try:
                scores[sid] = fut.result()
            except Exception:
                scores[sid] = 0
    except (TimeoutError, concurrent.futures.TimeoutError):
        logger.warning("[sample_score] timed out; returning partial scores")
        for f in futures:
            f.cancel()
    finally:
        executor.shutdown(wait=False)
        pool.closeall()
    return scores


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
    """Fetch study-header dicts for matched IDs in one batch query (hundreds of
    ids on a deep search — the old per-id loop was serial round trips), tagging
    each with its search origin."""
    studies = _fetch_study_headers(ids)
    for s in studies:
        s["via"] = "sample_metadata"
    return studies


def search_studies_by_field_filters(field_filters=None, keywords=None,
                                     data_types=None, exclude_ids=None,
                                     max_candidates=200, pool_size=16,
                                     resolved_pis=None):
    """Search studies whose sample metadata matches structured field filters or keywords.

    field_filters: [{"field": str, "value": str}] — checked via JSONB key lookup
    keywords:      [str] — checked across full JSONB text (any field)
    Returns matched studies in standard header dict shape with 'via': 'sample_metadata'.
    """
    ff  = [f for f in (field_filters or []) if f.get("field") and f.get("value")]
    kws = [k.strip() for k in (keywords or []) if k.strip()]
    if not ff and not kws:
        return []

    candidate_ids = _get_candidate_ids(
        data_types, exclude_ids, max_candidates, resolved_pis=resolved_pis,
    )
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
                                   pool_size=16, resolved_pis=None):
    """Search for studies whose sample metadata matches topic keywords.

    Uses a per-call ThreadedConnectionPool so parallel probes run on independent
    psycopg2 connections (the shared TRN singleton is not thread-safe).
    Returns matched studies in standard dict shape with 'via': 'sample_metadata'.
    """
    kws = [k.strip() for k in (topic_keywords or []) if k.strip()]
    if not kws:
        return []

    candidate_ids = _get_candidate_ids(
        data_types, exclude_ids, max_candidates, resolved_pis=resolved_pis,
    )
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
