"""All Qiita PostgreSQL query functions and sample-data helpers."""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from qiita_db.sql_connection import TRN

from config import REPORT_SAMPLE_LIMIT, PINNED_REPORT_CONTEXT_MAX_CHARS, PINNED_REPORT_MIN_PER_STUDY

_QIITA_BASE = os.environ.get("QIITA_BASE_DATA_DIR", "").rstrip("/")
from store import (
    PINNED_STUDIES_PER_CHAT_CAP,
    get_study_detail_cache,
    upsert_study_detail_cache,
    pin_study_to_chat,
    list_pinned_studies,
)
from helpers.llm_helpers import _truncate

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


def first_studies(limit=20):
    """Return deterministic first studies by study_id from PostgreSQL."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(100, limit))

    with TRN:
        sql = """
        SELECT DISTINCT s.study_id, s.study_title, s.study_abstract,
               s.study_alias, s.metadata_complete,
               sp_pi.name as pi_name, sp_pi.email as pi_email,
               sp_pi.affiliation as pi_affiliation,
               sp_lab.name as lab_person_name,
               (SELECT COUNT(*)
                FROM qiita.study_sample ss
                WHERE ss.study_id = s.study_id) AS num_samples,
               (SELECT STRING_AGG(DISTINCT dt2.data_type, ', ')
                FROM qiita.study_prep_template spt2
                JOIN qiita.prep_template pt2 ON spt2.prep_template_id = pt2.prep_template_id
                JOIN qiita.data_type dt2 ON pt2.data_type_id = dt2.data_type_id
                WHERE spt2.study_id = s.study_id) AS data_types,
               (SELECT COUNT(DISTINCT spt3.prep_template_id)
                FROM qiita.study_prep_template spt3
                WHERE spt3.study_id = s.study_id) AS num_preps
        FROM qiita.study s
        LEFT JOIN qiita.study_person sp_pi
            ON s.principal_investigator_id = sp_pi.study_person_id
        LEFT JOIN qiita.study_person sp_lab
            ON s.lab_person_id = sp_lab.study_person_id
        WHERE EXISTS (
            SELECT 1 FROM qiita.study_artifact sa
            JOIN qiita.artifact a ON sa.artifact_id = a.artifact_id
            JOIN qiita.visibility v ON a.visibility_id = v.visibility_id
            WHERE sa.study_id = s.study_id AND v.visibility = 'public'
        )
        AND EXISTS (
            SELECT 1 FROM qiita.per_study_tags pst
            WHERE pst.study_id = s.study_id AND pst.study_tag = 'GOLD'
        )
        ORDER BY s.study_id
        LIMIT %s
        """
        TRN.add(sql, [limit])
        results = TRN.execute_fetchindex()

    if not results:
        return []

    return [
        {
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
            "is_gold":         True,
        }
        for row in results
    ]


def _qiita_fetch(sql, params=(), default=None):
    """Run a Qiita-DB SELECT inside a TRN; return rows or `default` on error/empty."""
    try:
        with TRN:
            TRN.add(sql, list(params))
            rows = TRN.execute_fetchindex()
        return rows if rows else (default if default is not None else [])
    except Exception:
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


def _fetch_sample_context_text(study_id: int, max_chars: int = 3500) -> str:
    """Fetch all sample metadata fields from Qiita and return compact context text."""
    study_id = int(study_id)
    cnt      = _qiita_fetch(
        "SELECT COUNT(*) FROM qiita.study_sample WHERE study_id = %s",
        [study_id],
    )
    total = cnt[0][0] if cnt else 0

    rows = _qiita_fetch(
        f"""
        SELECT ss.sample_id, sm.sample_values
        FROM qiita.study_sample ss
        JOIN qiita.sample_{study_id} sm ON ss.sample_id = sm.sample_id
        WHERE ss.study_id = %s
          AND ss.sample_id <> 'qiita_sample_column_names'
        ORDER BY ss.sample_id
        LIMIT 200
        """,
        [study_id],
    )
    samples = [{"sample_id": r[0], "fields": dict(r[1])} for r in rows]
    return _build_samples_context_text(samples, total, max_chars=max_chars)


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


def _get_or_fetch_full_samples(study_id: int, limit: int = REPORT_SAMPLE_LIMIT):
    """Return cached full sample rows for a study, falling back to a Qiita fetch + cache write."""
    cached = get_study_detail_cache(study_id)
    if cached and cached.get("full_samples_json"):
        try:
            samples = json.loads(cached["full_samples_json"])
            if isinstance(samples, list):
                return samples
        except Exception:
            pass
    samples = _fetch_full_sample_metadata(study_id, limit=limit)
    if samples:
        try:
            upsert_study_detail_cache(study_id, None, None, full_samples_json=json.dumps(samples))
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


def _build_full_samples_block(study_id: int, budget_chars: int):
    """Compact full-metadata block for one pinned study, clipped to budget_chars."""
    header      = _fetch_study_header_cached(study_id)
    samples     = _get_or_fetch_full_samples(study_id)
    title       = (header or {}).get("study_title") or "Untitled study"
    num_samples = (header or {}).get("num_samples") or (len(samples) if samples else 0)
    data_types  = (header or {}).get("data_types") or ""

    lines = [
        f"### Study {study_id}: {_truncate(title, 140)}",
        f"  Data Types: {data_types or 'Not available'} | Total samples: {num_samples} | In report: {len(samples)}",
    ]
    if not samples:
        lines.append("  _No sample metadata available._")
        return "\n".join(lines)

    skip_fields = {"qiita_study_id"}
    empty_vals  = {"none", "null", "nan", "not applicable", "not provided", ""}
    budget      = max(500, int(budget_chars))
    out         = "\n".join(lines) + "\n"
    truncated_at = None
    for idx, sample in enumerate(samples):
        sid    = sample.get("sample_id", "?")
        fields = sample.get("fields") or {}
        parts  = []
        for k, v in sorted(fields.items()):
            if k in skip_fields or v is None:
                continue
            val = str(v).strip()
            if not val or val.lower() in empty_vals:
                continue
            parts.append(f"{k}={_truncate(val, 120)}")
        line = f"  {sid}: " + ", ".join(parts) + "\n"
        if len(out) + len(line) > budget:
            truncated_at = idx
            break
        out += line
    if truncated_at is not None:
        out += f"  _(truncated: showed {truncated_at} of {len(samples)} samples due to context budget)_\n"
    return out.rstrip()


def _build_pinned_reports_context(study_ids):
    """Build a 'PINNED STUDY REPORTS' context block from the given pinned study IDs."""
    if not study_ids:
        return None
    per_study = max(PINNED_REPORT_MIN_PER_STUDY, PINNED_REPORT_CONTEXT_MAX_CHARS // max(1, len(study_ids)))
    with ThreadPoolExecutor(max_workers=min(len(study_ids), 4)) as pool:
        blocks = list(pool.map(lambda sid: _build_full_samples_block(sid, per_study), study_ids))
    header = (
        "PINNED STUDY REPORTS (full sample-level metadata for studies the user attached via /report):\n"
        "Use these for per-sample questions and cross-study comparisons.\n"
    )
    body = "\n\n".join(b for b in blocks if b)
    text = header + body
    if len(text) > PINNED_REPORT_CONTEXT_MAX_CHARS:
        cut  = text.rfind("\n", 0, PINNED_REPORT_CONTEXT_MAX_CHARS - 40)
        text = text[: max(cut, PINNED_REPORT_CONTEXT_MAX_CHARS - 40)] + "\n...(pinned context truncated)"
    return text


def _pin_studies_validated(chat_id: str, scope: str, study_ids: list):
    """Validate study IDs against Qiita, pin the valid ones, and return a summary.

    Returns (pinned_now, invalid, rejected, all_pinned) where:
      pinned_now  — IDs newly pinned this call
      invalid     — IDs not found / private in Qiita
      rejected    — IDs skipped because the 10-pin cap was reached
      all_pinned  — full list of pinned IDs for this chat after the operation
    """
    seen = set()
    deduped = [sid for sid in study_ids if not (sid in seen or seen.add(sid))]
    pinned_now, invalid, rejected = [], [], []
    for sid in deduped:
        header = _fetch_study_header_cached(sid)
        if header is None or ((header.get("num_samples") or 0) == 0 and (header.get("num_preps") or 0) == 0):
            invalid.append(sid)
        else:
            ok = pin_study_to_chat(chat_id, scope, sid)
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
    rows     = _qiita_fetch(
        """
        SELECT s.study_id, s.study_title, s.study_abstract,
               s.study_alias, s.metadata_complete,
               sp_pi.name AS pi_name, sp_pi.email AS pi_email,
               sp_pi.affiliation AS pi_affiliation,
               sp_lab.name AS lab_person_name,
               (SELECT COUNT(*) FROM qiita.study_sample ss WHERE ss.study_id = s.study_id) AS num_samples,
               (SELECT STRING_AGG(DISTINCT dt2.data_type, ', ')
                FROM qiita.study_prep_template spt2
                JOIN qiita.prep_template pt2 ON spt2.prep_template_id = pt2.prep_template_id
                JOIN qiita.data_type dt2 ON pt2.data_type_id = dt2.data_type_id
                WHERE spt2.study_id = s.study_id) AS data_types,
               (SELECT COUNT(DISTINCT spt3.prep_template_id)
                FROM qiita.study_prep_template spt3
                WHERE spt3.study_id = s.study_id) AS num_preps
        FROM qiita.study s
        LEFT JOIN qiita.study_person sp_pi ON s.principal_investigator_id = sp_pi.study_person_id
        LEFT JOIN qiita.study_person sp_lab ON s.lab_person_id = sp_lab.study_person_id
        WHERE s.study_id = %s
        """,
        [study_id],
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "study_id":        r[0],
        "study_title":     r[1],
        "study_abstract":  r[2],
        "study_alias":     r[3],
        "metadata_complete": r[4],
        "pi_name":         r[5],
        "pi_email":        r[6],
        "pi_affiliation":  r[7],
        "lab_person_name": r[8],
        "num_samples":     r[9],
        "data_types":      r[10],
        "num_preps":       r[11],
    }


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

    artifacts = [
        {
            "prep_template_id": r[0],
            "prep_name":        r[1],
            "artifact_id":      r[2],
            "artifact_type":    r[3],
            "data_type":        r[4],
            "full_path":        _abs(r[5]),
            "generated_timestamp": str(r[6]) if r[6] else None,
        }
        for r in artifact_rows
    ]
    return preps, artifacts
