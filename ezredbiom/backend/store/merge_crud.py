"""CRUD operations for merge_workspaces, merge_workspace_studies, and merge_jobs."""

import json
import uuid
from typing import Optional

from .db import _conn, _as_dict, _now

_MAX_STUDIES = 5


def create_workspace(user_id: str, name: str) -> dict:
    workspace_id = str(uuid.uuid4())[:12]
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO merge_workspaces(workspace_id, user_id, name, created_at, updated_at) VALUES(?,?,?,?,?)",
            (workspace_id, user_id, name, now, now),
        )
        conn.commit()
    return {"workspace_id": workspace_id, "user_id": user_id, "name": name,
            "created_at": now, "updated_at": now, "studies": []}


def list_workspaces(user_id: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM merge_workspaces WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [_as_dict(r) for r in rows]


def get_workspace(workspace_id: str, user_id: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM merge_workspaces WHERE workspace_id=? AND user_id=?",
            (workspace_id, user_id),
        ).fetchone()
        if row is None:
            return None
        ws = _as_dict(row)
        studies = conn.execute(
            "SELECT * FROM merge_workspace_studies WHERE workspace_id=? ORDER BY added_at",
            (workspace_id,),
        ).fetchall()
    ws["studies"] = [_as_dict(s) for s in studies]
    return ws


def delete_workspace(workspace_id: str, user_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM merge_workspaces WHERE workspace_id=? AND user_id=?",
            (workspace_id, user_id),
        )
        conn.commit()
    return cur.rowcount > 0


def add_study_to_workspace(workspace_id: str, study: dict) -> Optional[dict]:
    """Returns None if workspace already has _MAX_STUDIES studies."""
    now = _now()
    with _conn() as conn:
        count = conn.execute(
            "SELECT COUNT(1) FROM merge_workspace_studies WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()[0]
        if count >= _MAX_STUDIES:
            return None
        conn.execute(
            """INSERT OR IGNORE INTO merge_workspace_studies
               (workspace_id, study_id, study_title, data_types, num_samples,
                chosen_artifact_id, sample_filter, added_at)
               VALUES(?,?,?,?,?,NULL,NULL,?)""",
            (workspace_id, int(study["study_id"]), study.get("study_title"),
             study.get("data_types"), study.get("num_samples"), now),
        )
        conn.execute(
            "UPDATE merge_workspaces SET updated_at=? WHERE workspace_id=?",
            (now, workspace_id),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM merge_workspace_studies WHERE workspace_id=? ORDER BY added_at",
            (workspace_id,),
        ).fetchall()
    return [_as_dict(r) for r in rows]


def remove_study_from_workspace(workspace_id: str, study_id: int) -> list:
    now = _now()
    with _conn() as conn:
        conn.execute(
            "DELETE FROM merge_workspace_studies WHERE workspace_id=? AND study_id=?",
            (workspace_id, int(study_id)),
        )
        conn.execute(
            "UPDATE merge_workspaces SET updated_at=? WHERE workspace_id=?",
            (now, workspace_id),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM merge_workspace_studies WHERE workspace_id=? ORDER BY added_at",
            (workspace_id,),
        ).fetchall()
    return [_as_dict(r) for r in rows]


def update_workspace_study(workspace_id: str, study_id: int, *,
                           chosen_artifact_id=None, sample_filter=None) -> list:
    now = _now()
    sample_filter_str = json.dumps(sample_filter) if isinstance(sample_filter, list) else sample_filter
    with _conn() as conn:
        conn.execute(
            """UPDATE merge_workspace_studies
               SET chosen_artifact_id=?, sample_filter=?
               WHERE workspace_id=? AND study_id=?""",
            (chosen_artifact_id, sample_filter_str, workspace_id, int(study_id)),
        )
        conn.execute(
            "UPDATE merge_workspaces SET updated_at=? WHERE workspace_id=?",
            (now, workspace_id),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM merge_workspace_studies WHERE workspace_id=? ORDER BY added_at",
            (workspace_id,),
        ).fetchall()
    return [_as_dict(r) for r in rows]


def create_merge_job(workspace_id: str, user_id: str, workspace_snap: list) -> dict:
    job_id = str(uuid.uuid4())
    now = _now()
    snap_json = json.dumps(workspace_snap)
    with _conn() as conn:
        conn.execute(
            """INSERT INTO merge_jobs(job_id, workspace_id, user_id, status,
               workspace_snap, created_at, updated_at)
               VALUES(?,?,?,'pending',?,?,?)""",
            (job_id, workspace_id, user_id, snap_json, now, now),
        )
        conn.commit()
    return {"job_id": job_id, "workspace_id": workspace_id, "user_id": user_id,
            "status": "pending", "created_at": now, "updated_at": now}


def get_merge_job(job_id: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM merge_jobs WHERE job_id=?", (job_id,)).fetchone()
    return _as_dict(row)


def list_merge_jobs(workspace_id: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM merge_jobs WHERE workspace_id=? ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()
    return [_as_dict(r) for r in rows]


def update_merge_job_status(job_id: str, status: str, *,
                            error_message: str = None, result_path: str = None) -> bool:
    now = _now()
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE merge_jobs SET status=?, error_message=?, result_path=?, updated_at=?
               WHERE job_id=?""",
            (status, error_message, result_path, now, job_id),
        )
        conn.commit()
    return cur.rowcount > 0
