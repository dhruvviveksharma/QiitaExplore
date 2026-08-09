"""Study caching, project summaries, and pinned-study management."""

from datetime import datetime

from .db import _conn, _as_dict, _now, _resolve_user
from .crud import _project_exists

SCOPE_PROJECT = "project"
SCOPE_GLOBAL  = "global"
PINNED_STUDIES_PER_CHAT_CAP = 10

_STUDY_DETAIL_CACHE_TTL_HOURS = 6


def upsert_project_study_summary(project_id: str, user_id: str, study_id: int, summary_text: str):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        if not _project_exists(conn, project_id, resolved_user):
            return False
        conn.execute(
            """
            UPDATE project_studies
            SET summary_text = ?, updated_at = ?
            WHERE project_id = ? AND study_id = ?
            """,
            (summary_text or "", _now(), project_id, int(study_id)),
        )
        conn.commit()
    return True


def get_project_context_summary(project_id: str, user_id: str):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        if not _project_exists(conn, project_id, resolved_user):
            return None
        row = conn.execute(
            "SELECT summary_text, source_updated_at, created_at, updated_at FROM project_context_summaries WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    return _as_dict(row)


def upsert_project_context_summary(project_id: str, user_id: str, summary_text: str, source_updated_at: str = None):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        if not _project_exists(conn, project_id, resolved_user):
            return False
        now = _now()
        conn.execute(
            """
            INSERT INTO project_context_summaries(project_id, summary_text, source_updated_at, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                summary_text = excluded.summary_text,
                source_updated_at = excluded.source_updated_at,
                updated_at = excluded.updated_at
            """,
            (project_id, summary_text or "", source_updated_at, now, now),
        )
        conn.commit()
    return True


def update_project_study_data(
    project_id: str,
    study_id: int,
    *,
    data_types=None,
    num_samples=None,
    num_preps=None,
    preps_json=None,
):
    """Update enriched columns for a project_studies row (COALESCE — only overwrites NULLs)."""
    with _conn() as conn:
        conn.execute(
            """
            UPDATE project_studies
            SET data_types  = COALESCE(?, data_types),
                num_samples = COALESCE(?, num_samples),
                num_preps   = COALESCE(?, num_preps),
                preps_json  = COALESCE(?, preps_json),
                updated_at  = ?
            WHERE project_id = ? AND study_id = ?
            """,
            (data_types, num_samples, num_preps, preps_json, _now(), project_id, int(study_id)),
        )
        conn.commit()
    return True


def get_study_detail_cache(study_id: int):
    """Return cached study detail if it exists and is less than TTL hours old, else None."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT preps_json, artifacts_json, samples_context, full_samples_json, artifact_graph_json, "
            "prep_metadata_json, samples_json, total_samples, cached_at FROM study_detail_cache WHERE study_id = ?",
            (int(study_id),),
        ).fetchone()
    if row is None:
        return None
    cached_at = row["cached_at"]
    if cached_at:
        try:
            age = datetime.utcnow() - datetime.fromisoformat(cached_at.rstrip("Z"))
            if age.total_seconds() > _STUDY_DETAIL_CACHE_TTL_HOURS * 3600:
                return None
        except Exception:
            pass
    return _as_dict(row)


def upsert_study_detail_cache(
    study_id: int,
    preps_json: str,
    artifacts_json: str,
    samples_context: str = None,
    full_samples_json: str = None,
    artifact_graph_json: str = None,
    prep_metadata_json: str = None,
    samples_json: str = None,
    total_samples: int = None,
    full_samples_limit: int = None,
):
    """Cache study detail. Pass None for any field to preserve the existing value (COALESCE)."""
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO study_detail_cache(
                study_id, preps_json, artifacts_json, samples_context, full_samples_json,
                artifact_graph_json, prep_metadata_json, samples_json, total_samples,
                full_samples_limit, cached_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(study_id) DO UPDATE SET
                preps_json          = COALESCE(excluded.preps_json,          study_detail_cache.preps_json),
                artifacts_json      = COALESCE(excluded.artifacts_json,      study_detail_cache.artifacts_json),
                samples_context     = COALESCE(excluded.samples_context,     study_detail_cache.samples_context),
                full_samples_json   = COALESCE(excluded.full_samples_json,   study_detail_cache.full_samples_json),
                artifact_graph_json = COALESCE(excluded.artifact_graph_json, study_detail_cache.artifact_graph_json),
                prep_metadata_json  = COALESCE(excluded.prep_metadata_json,  study_detail_cache.prep_metadata_json),
                samples_json        = COALESCE(excluded.samples_json,       study_detail_cache.samples_json),
                total_samples       = COALESCE(excluded.total_samples,      study_detail_cache.total_samples),
                full_samples_limit  = COALESCE(excluded.full_samples_limit, study_detail_cache.full_samples_limit),
                cached_at           = excluded.cached_at
            """,
            (int(study_id), preps_json, artifacts_json, samples_context, full_samples_json,
             artifact_graph_json, prep_metadata_json, samples_json, total_samples,
             full_samples_limit, _now()),
        )
        conn.commit()
    return True


def get_biom_sample_cache(artifact_id: int):
    with _conn() as conn:
        row = conn.execute(
            "SELECT num_samples, sample_ids_json, cached_at FROM biom_sample_cache WHERE artifact_id = ?",
            (int(artifact_id),),
        ).fetchone()
    return _as_dict(row)


def upsert_biom_sample_cache(artifact_id: int, num_samples: int, sample_ids_json: str):
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO biom_sample_cache(artifact_id, num_samples, sample_ids_json, cached_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                num_samples     = excluded.num_samples,
                sample_ids_json = excluded.sample_ids_json,
                cached_at       = excluded.cached_at
            """,
            (int(artifact_id), num_samples, sample_ids_json, _now()),
        )
        conn.commit()
    return True


def _normalize_scope(scope: str) -> str:
    s = (scope or "").strip()
    return s if s in (SCOPE_PROJECT, SCOPE_GLOBAL) else SCOPE_PROJECT


def _load_pinned_study_meta(conn, chat_id: str, scope: str):
    """Pinned studies for a chat, with the denormalized title.

    The single read of this table — `_load_pinned_studies` derives from it rather
    than running a second query over the same rows. `study_title` is NULL for rows
    pinned before the column existed; callers fall back to the bare study ID.

    Project-scope reads join current project membership so stale pins for
    removed studies never surface in UI or context assembly.
    """
    scope = _normalize_scope(scope)
    if scope == SCOPE_PROJECT:
        rows = conn.execute(
            """
            SELECT p.study_id, p.study_title
            FROM chat_pinned_studies p
            JOIN project_chats pc ON pc.chat_id = p.chat_id
            JOIN project_studies ps ON ps.project_id = pc.project_id AND ps.study_id = p.study_id
            WHERE p.chat_id = ? AND p.chat_scope = ?
            ORDER BY p.pinned_at ASC
            """,
            (chat_id, scope),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT study_id, study_title FROM chat_pinned_studies
            WHERE chat_id = ? AND chat_scope = ? ORDER BY pinned_at ASC
            """,
            (chat_id, scope),
        ).fetchall()
    return [{"study_id": int(r["study_id"]), "study_title": r["study_title"]} for r in rows]


def _load_pinned_studies(conn, chat_id: str, scope: str):
    return [m["study_id"] for m in _load_pinned_study_meta(conn, chat_id, scope)]


def pin_study_to_chat(chat_id: str, scope: str, study_id: int, study_title: str = None):
    """Attach a study to a chat. Caps at PINNED_STUDIES_PER_CHAT_CAP.

    `study_title` is denormalized here so the composer chip can name the study
    without a Postgres round-trip every time a chat is opened.
    """
    scope = _normalize_scope(scope)
    with _conn() as conn:
        existing = _load_pinned_studies(conn, chat_id, scope)
        if int(study_id) in existing:
            return True
        if len(existing) >= PINNED_STUDIES_PER_CHAT_CAP:
            return False
        conn.execute(
            """
            INSERT OR IGNORE INTO chat_pinned_studies(chat_id, chat_scope, study_id, study_title, pinned_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (chat_id, scope, int(study_id), study_title, _now()),
        )
        conn.commit()
    return True


def unpin_study_from_chat(chat_id: str, scope: str, study_id: int):
    with _conn() as conn:
        conn.execute(
            "DELETE FROM chat_pinned_studies WHERE chat_id = ? AND chat_scope = ? AND study_id = ?",
            (chat_id, _normalize_scope(scope), int(study_id)),
        )
        conn.commit()
    return True


def list_pinned_studies(chat_id: str, scope: str):
    with _conn() as conn:
        return _load_pinned_studies(conn, chat_id, scope)


def list_pinned_study_meta(chat_id: str, scope: str):
    with _conn() as conn:
        return _load_pinned_study_meta(conn, chat_id, scope)
