"""All Qiita PostgreSQL query functions and sample-data helpers."""

import json
import logging
import os
import re
import time

from helpers.pg_pool import pooled_fetchall
from helpers.llm_helpers import _truncate

from config import REPORT_SAMPLE_LIMIT
from store import (
    get_study_detail_cache,
    upsert_study_detail_cache,
    pin_study_to_chat,
    list_pinned_studies,
    SCOPE_PROJECT,
    get_project_id_for_chat,
    get_project_studies_only,
)

_QIITA_BASE = os.environ.get("QIITA_BASE_DATA_DIR", "").rstrip("/")

logger = logging.getLogger(__name__)


def _build_samples_context_text(samples_with_values: list, total: int, max_chars: int = 3500) -> str:
    """Format sample metadata dicts into a compact LLM-readable text block."""
    if not samples_with_values:
        return ""
    _skip = {"qiita_study_id"}
    lines  = [f"  Samples ({total} total, showing {len(samples_with_values)}):"]
    for s in samples_with_values:
        sid    = s.get("sample_id", "?")
        fields = s.get("fields") or {}
        parts  = []
        for k, v in sorted(fields.items()):
            if k in _skip or v is None:
                continue
            val = str(v).strip()
            if not val or val.lower() in ("none", "null", "nan", "not applicable", "not provided"):
                continue
            parts.append(f"{k}={_truncate(val, 60)}")
        lines.append(f"    {sid}: " + ", ".join(parts))
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text


# Shared by qiita_fetch._build_study_header_query and
# services.study_service.search_studies_with_sql — both SELECT the same
# per-study aggregate columns, just under different WHERE/visibility logic.
_STUDY_COUNT_COLUMNS = """(SELECT COUNT(*)
            FROM qiita.study_sample ss
            WHERE ss.study_id = s.study_id) AS num_samples,
           (SELECT STRING_AGG(DISTINCT dt2.data_type, ', ')
            FROM qiita.study_prep_template spt2
            JOIN qiita.prep_template pt2 ON spt2.prep_template_id = pt2.prep_template_id
            JOIN qiita.data_type dt2 ON pt2.data_type_id = dt2.data_type_id
            WHERE spt2.study_id = s.study_id) AS data_types,
           (SELECT COUNT(DISTINCT spt3.prep_template_id)
            FROM qiita.study_prep_template spt3
            WHERE spt3.study_id = s.study_id) AS num_preps"""

# Public-visibility gate shared by _build_study_header_query,
# services.study_service.search_studies_with_sql, and
# helpers.sample_search._get_candidate_ids — a correlated EXISTS has no
# artifact fan-out, so callers need no DISTINCT.
_PUBLIC_ARTIFACT_EXISTS = """EXISTS (
        SELECT 1 FROM qiita.study_artifact sa
        JOIN qiita.artifact a ON sa.artifact_id = a.artifact_id
        JOIN qiita.visibility v ON a.visibility_id = v.visibility_id
        WHERE sa.study_id = s.study_id AND v.visibility = 'public'
    )"""


def _build_study_header_query(distinct=False):
    """Shared SELECT/FROM/JOIN for study-header rows. Caller appends its own WHERE/ORDER/LIMIT."""
    return f"""
    SELECT {"DISTINCT " if distinct else ""}s.study_id, s.study_title, s.study_abstract,
           s.study_alias, s.metadata_complete,
           sp_pi.name as pi_name, sp_pi.email as pi_email,
           sp_pi.affiliation as pi_affiliation,
           sp_lab.name as lab_person_name,
           {_STUDY_COUNT_COLUMNS}
    FROM qiita.study s
    LEFT JOIN qiita.study_person sp_pi
        ON s.principal_investigator_id = sp_pi.study_person_id
    LEFT JOIN qiita.study_person sp_lab
        ON s.lab_person_id = sp_lab.study_person_id
    WHERE {_PUBLIC_ARTIFACT_EXISTS}
    """


def _row_to_study_header(row):
    return {
        "study_id":        row[0],
        "study_title":     row[1],
        "study_abstract":  row[2],
        "study_alias":     row[3],
        "metadata_complete": row[4],
        "pi_name":         row[5],
        "pi_email":        row[6],
        "pi_affiliation":  row[7],
        "lab_person_name": row[8],
        "num_samples":     row[9],
        "data_types":      row[10],
        "num_preps":       row[11],
    }


def first_studies(limit=20):
    """Return deterministic first studies by study_id from PostgreSQL."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(100, limit))

    sql = _build_study_header_query(distinct=True) + """
    AND EXISTS (
        SELECT 1 FROM qiita.per_study_tags pst
        WHERE pst.study_id = s.study_id AND pst.study_tag = 'GOLD'
    )
    ORDER BY s.study_id
    LIMIT %s
    """
    results = pooled_fetchall(sql, [limit])

    if not results:
        return []

    return [{**_row_to_study_header(row), "is_gold": True} for row in results]


def _qiita_fetch(sql, params=(), default=None):
    """Run a Qiita-DB SELECT on a pooled connection; return rows or `default` on error/empty."""
    try:
        rows = pooled_fetchall(sql, list(params))
        return rows if rows else (default if default is not None else [])
    except Exception:
        # Never silently: a Postgres failure here is otherwise indistinguishable
        # from "no rows", which is how a broken pin path stayed invisible.
        logger.exception("_qiita_fetch failed: %s", " ".join(sql.split())[:160])
        return default if default is not None else []


def is_study_public(study_id: int) -> bool:
    """Return True only if the study has at least one public artifact."""
    rows = _qiita_fetch(
        """SELECT 1 FROM qiita.study_artifact sa
           JOIN qiita.artifact a ON sa.artifact_id = a.artifact_id
           JOIN qiita.visibility v ON a.visibility_id = v.visibility_id
           WHERE sa.study_id = %s AND v.visibility = 'public'
           LIMIT 1""",
        [int(study_id)],
    )
    return bool(rows)


def _fetch_study_samples(study_id: int, limit: int = 200):
    """Return sample list for a study using dynamic sample_{study_id} table."""
    study_id = int(study_id)
    cnt      = _qiita_fetch(
        "SELECT COUNT(*) FROM qiita.study_sample WHERE study_id = %s",
        [study_id],
    )
    total = cnt[0][0] if cnt else 0

    rows = _qiita_fetch(
        f"""
        SELECT ss.sample_id,
               sm.sample_values->>'anonymized_name'      AS anonymized_name,
               sm.sample_values->>'collection_timestamp' AS collection_timestamp,
               sm.sample_values->>'env_package'          AS env_package
        FROM qiita.study_sample ss
        JOIN qiita.sample_{study_id} sm ON ss.sample_id = sm.sample_id
        WHERE ss.study_id = %s
        ORDER BY ss.sample_id
        LIMIT %s
        """,
        [study_id, limit],
    )
    samples = [
        {
            "sample_id":            r[0],
            "anonymized_name":      r[1],
            "collection_timestamp": r[2],
            "env_package":          r[3],
        }
        for r in rows
    ]
    return samples, total


def _fetch_prep_metadata_summary(prep_template_id: int):
    """Return one row of sequencing metadata for a prep template."""
    prep_template_id = int(prep_template_id)
    rows = _qiita_fetch(
        f"""
        SELECT pm.sample_values->>'platform'           AS platform,
               pm.sample_values->>'target_gene'        AS target_gene,
               pm.sample_values->>'instrument_model'   AS instrument_model,
               pm.sample_values->>'target_subfragment' AS target_subfragment
        FROM qiita.prep_template_sample pts
        JOIN qiita.prep_{prep_template_id} pm ON pts.sample_id = pm.sample_id
        WHERE pts.prep_template_id = %s
        LIMIT 1
        """,
        [prep_template_id],
    )
    if not rows:
        return {}
    r = rows[0]
    return {
        "platform":          r[0],
        "target_gene":       r[1],
        "instrument_model":  r[2],
        "target_subfragment": r[3],
    }


def _fetch_full_sample_metadata(study_id: int, limit: int = REPORT_SAMPLE_LIMIT):
    """Return sample metadata rows as [{sample_id, fields}] capped to limit."""
    study_id = int(study_id)
    limit    = max(1, int(limit))
    rows     = _qiita_fetch(
        f"""
        SELECT ss.sample_id, sm.sample_values
        FROM qiita.study_sample ss
        JOIN qiita.sample_{study_id} sm ON ss.sample_id = sm.sample_id
        WHERE ss.study_id = %s
          AND ss.sample_id <> 'qiita_sample_column_names'
        ORDER BY ss.sample_id
        LIMIT %s
        """,
        [study_id, limit],
    )
    return [{"sample_id": r[0], "fields": dict(r[1])} for r in rows]


def _fetch_sample_context_text(study_id: int, max_chars: int = 3500) -> str:
    """Fetch all sample metadata fields from Qiita and return compact context text."""
    study_id = int(study_id)
    cnt      = _qiita_fetch(
        "SELECT COUNT(*) FROM qiita.study_sample WHERE study_id = %s",
        [study_id],
    )
    total   = cnt[0][0] if cnt else 0
    samples = _fetch_full_sample_metadata(study_id, limit=200)
    return _build_samples_context_text(samples, total, max_chars=max_chars)


def _get_or_fetch_full_samples(study_id: int, limit: int = REPORT_SAMPLE_LIMIT):
    """Return cached full sample rows for a study, falling back to a Qiita fetch + cache write.

    Sufficiency is decided against the limit the cached rows were fetched at, not
    against a row count from a different query. `num_samples` counts
    `qiita.study_sample` including the `qiita_sample_column_names` sentinel, while
    these rows exclude it and inner-join `sample_{id}` — so comparing the two made
    the cache permanently unusable for any study below the limit.
    """
    cached = get_study_detail_cache(study_id) or {}
    if cached.get("full_samples_json"):
        try:
            samples = json.loads(cached["full_samples_json"])
            cached_limit = cached.get("full_samples_limit") or 0
            # Enough rows for this request, or the fetch that wrote them came back
            # short of its own limit, meaning it exhausted the study.
            if isinstance(samples, list) and (cached_limit >= limit or len(samples) < cached_limit):
                return samples[:limit]
        except Exception:
            pass
    samples = _fetch_full_sample_metadata(study_id, limit=limit)
    if samples:
        try:
            upsert_study_detail_cache(study_id, None, None,
                                      full_samples_json=json.dumps(samples),
                                      full_samples_limit=limit)
        except Exception:
            pass
    return samples


_STUDY_HEADER_TTL_SECONDS = 3600
_study_header_cache = {}  # study_id -> (fetched_at_epoch, header_dict_or_None)


def _fetch_study_header_cached(study_id: int):
    """TTL-memoized wrapper around _fetch_study_header (hot path for pinned context)."""
    sid   = int(study_id)
    now   = time.time()
    entry = _study_header_cache.get(sid)
    if entry and now - entry[0] < _STUDY_HEADER_TTL_SECONDS:
        return entry[1]
    header                  = _fetch_study_header(sid)
    _study_header_cache[sid] = (now, header)
    return header


def _pin_studies_validated(chat_id: str, scope: str, study_ids: list):
    """Validate study IDs against Qiita, pin the valid ones, and return a summary.

    Returns (pinned_now, invalid, rejected, all_pinned) where:
      pinned_now  — IDs newly pinned this call
      invalid     — IDs not found / private in Qiita, or not in project (project scope)
      rejected    — IDs skipped because the 10-pin cap was reached
      all_pinned  — full list of pinned IDs for this chat after the operation
    """
    seen = set()
    deduped = [int(sid) for sid in study_ids if not (sid in seen or seen.add(sid))]
    invalid = []
    if scope == SCOPE_PROJECT:
        project_id = get_project_id_for_chat(chat_id)
        allowed = set()
        if project_id:
            proj = get_project_studies_only(project_id) or {}
            allowed = {
                int(s["study_id"])
                for s in (proj.get("studies") or [])
                if s.get("study_id") is not None
            }
        out_of_project = [sid for sid in deduped if sid not in allowed]
        deduped = [sid for sid in deduped if sid in allowed]
        invalid.extend(out_of_project)
    pinned_now, rejected = [], []
    for sid in deduped:
        header = _fetch_study_header_cached(sid)
        if header is None or ((header.get("num_samples") or 0) == 0 and (header.get("num_preps") or 0) == 0):
            logger.warning("pin rejected study=%s: header=%s", sid, "missing" if header is None else "no samples/preps")
            invalid.append(sid)
        else:
            ok = pin_study_to_chat(chat_id, scope, sid, header.get("study_title"))
            (pinned_now if ok else rejected).append(sid)
    all_pinned = list_pinned_studies(chat_id, scope)
    return pinned_now, invalid, rejected, all_pinned


def _build_samples_report_payload(study_id: int, sample_limit: int = REPORT_SAMPLE_LIMIT):
    """Build the structured payload rendered as an inline samples-browser in the chat bubble."""
    study_id = int(study_id)
    header   = _fetch_study_header_cached(study_id) or {}
    if (header.get("num_samples") or 0) == 0 and (header.get("num_preps") or 0) == 0:
        raise ValueError(f"Study {study_id} is private or has no accessible data")
    samples  = _get_or_fetch_full_samples(study_id, limit=sample_limit) or []
    return {
        "kind": "samples_report",
        "study_id": study_id,
        "header": {
            "study_id":       study_id,
            "study_title":    header.get("study_title") or "Untitled study",
            "study_abstract": header.get("study_abstract"),
            "pi_name":        header.get("pi_name"),
            "pi_affiliation": header.get("pi_affiliation"),
            "num_samples":    header.get("num_samples"),
            "data_types":     header.get("data_types"),
            "num_preps":      header.get("num_preps"),
        },
        "samples": samples,
    }


def _detect_mentioned_study_ids(user_content: str, proj) -> list:
    """Return project study IDs explicitly mentioned in user_content.

    Matches 'study 77', 'study ID 77', '#77'. Only returns IDs that exist
    in the project to avoid false positives on unrelated numbers.
    """
    project_study_ids = {
        int(s["study_id"])
        for s in ((proj or {}).get("studies") or [])
        if s.get("study_id") is not None
    }
    if not project_study_ids:
        return []
    found = set()
    for m in re.finditer(r'\b(?:study\s+(?:id\s+)?|#)(\d+)\b', user_content, re.IGNORECASE):
        sid = int(m.group(1))
        if sid in project_study_ids:
            found.add(sid)
    return sorted(found)


def _fetch_study_header(study_id: int):
    """Fetch one study header row for deterministic study report output."""
    study_id = int(study_id)
    sql = _build_study_header_query() + " AND s.study_id = %s"
    rows = _qiita_fetch(sql, [study_id])
    if not rows:
        return None
    return _row_to_study_header(rows[0])


def _fetch_study_headers(study_ids):
    """Batch variant of _fetch_study_header: one query for many ids, results
    returned in the caller's id order (missing/non-public ids dropped)."""
    ids = [int(s) for s in study_ids]
    if not ids:
        return []
    sql = _build_study_header_query() + " AND s.study_id = ANY(%s)"
    rows = _qiita_fetch(sql, [ids])
    by_id = {row[0]: _row_to_study_header(row) for row in rows}
    return [by_id[sid] for sid in ids if sid in by_id]


def _fetch_study_detail_from_qiita(study_id: int):
    """Run prep and artifact queries for a study and return (preps, artifacts)."""
    prep_rows = _qiita_fetch(
        """
        SELECT pt.prep_template_id, pt.name AS prep_name,
               dt.data_type, pt.investigation_type,
               pt.preprocessing_status,
               pt.creation_timestamp, pt.modification_timestamp
        FROM qiita.study_prep_template spt
        JOIN qiita.prep_template pt ON spt.prep_template_id = pt.prep_template_id
        JOIN qiita.data_type dt ON pt.data_type_id = dt.data_type_id
        WHERE spt.study_id = %s
        ORDER BY pt.prep_template_id
        """,
        [study_id],
    )
    preps = [
        {
            "prep_template_id":    r[0],
            "prep_name":           r[1],
            "data_type":           r[2],
            "investigation_type":  r[3],
            "preprocessing_status": r[4],
            "creation_timestamp":  str(r[5]) if r[5] else None,
            "modification_timestamp": str(r[6]) if r[6] else None,
        }
        for r in prep_rows
    ]

    artifact_rows = _qiita_fetch(
        """
        SELECT pt.prep_template_id, pt.name AS prep_name,
               a.artifact_id, at.artifact_type, dt.data_type,
               dd.mountpoint || '/' || a.artifact_id || '/' || f.filepath AS full_path,
               a.generated_timestamp
        FROM qiita.study_prep_template spt
        JOIN qiita.prep_template pt ON spt.prep_template_id = pt.prep_template_id
        JOIN qiita.data_type dt ON pt.data_type_id = dt.data_type_id
        JOIN qiita.preparation_artifact pa ON pt.prep_template_id = pa.prep_template_id
        JOIN qiita.artifact a ON pa.artifact_id = a.artifact_id
        JOIN qiita.artifact_type at ON a.artifact_type_id = at.artifact_type_id
        JOIN qiita.artifact_filepath af ON a.artifact_id = af.artifact_id
        JOIN qiita.filepath f ON af.filepath_id = f.filepath_id
        JOIN qiita.data_directory dd ON f.data_directory_id = dd.data_directory_id
        WHERE spt.study_id = %s
        ORDER BY pt.prep_template_id, a.artifact_id
        LIMIT 500
        """,
        [study_id],
    )
    def _abs(path):
        if path and not os.path.isabs(path) and _QIITA_BASE:
            return f"{_QIITA_BASE}/{path}"
        return path

    # One entry per artifact_id; prefer the .biom file path
    artifact_by_id: dict = {}
    for r in artifact_rows:
        aid = r[2]
        path = _abs(r[5])
        existing = artifact_by_id.get(aid)
        if existing is None or path.lower().endswith(".biom"):
            artifact_by_id[aid] = {
                "prep_template_id": r[0],
                "prep_name":        r[1],
                "artifact_id":      aid,
                "artifact_type":    r[3],
                "data_type":        r[4],
                "full_path":        path,
                "generated_timestamp": str(r[6]) if r[6] else None,
            }
    artifacts = list(artifact_by_id.values())
    return preps, artifacts
