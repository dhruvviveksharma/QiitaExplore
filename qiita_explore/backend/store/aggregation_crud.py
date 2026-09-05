"""CRUD for aggregations / aggregation_studies — a user's named set of studies
whose per_sample_FASTQ artifacts feed one QIIME2 manifest
(helpers/fastq_manifest.fetch_aggregate_manifest).

Own module for the same reason as merge_crud.py: store/crud.py sits at the
500-line cap. Every mutator returns the full aggregation so the frontend can
patch its state from the response body instead of re-fetching.
"""

import uuid
from typing import Optional

from .db import _conn, _as_dict, _now

AGGREGATION_STUDIES_CAP = 50


def _studies(conn, aggregation_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM aggregation_studies WHERE aggregation_id=? ORDER BY added_at, study_id",
        (aggregation_id,),
    ).fetchall()
    return [_as_dict(r) for r in rows]


def _owned(conn, aggregation_id: str, user_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM aggregations WHERE aggregation_id=? AND user_id=?",
        (aggregation_id, user_id),
    ).fetchone()
    return row is not None


def _touch(conn, aggregation_id: str, now: str) -> None:
    conn.execute(
        "UPDATE aggregations SET updated_at=? WHERE aggregation_id=?",
        (now, aggregation_id),
    )


def _get(conn, aggregation_id: str, user_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM aggregations WHERE aggregation_id=? AND user_id=?",
        (aggregation_id, user_id),
    ).fetchone()
    if row is None:
        return None
    agg = _as_dict(row)
    agg["studies"] = _studies(conn, aggregation_id)
    return agg


def create_aggregation(user_id: str, name: str) -> dict:
    aggregation_id = str(uuid.uuid4())[:12]
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO aggregations(aggregation_id, user_id, name, created_at, updated_at) VALUES(?,?,?,?,?)",
            (aggregation_id, user_id, name, now, now),
        )
        conn.commit()
    return {"aggregation_id": aggregation_id, "user_id": user_id, "name": name,
            "created_at": now, "updated_at": now, "studies": []}


def list_aggregations(user_id: str) -> list:
    """Every aggregation of user_id, each with its studies embedded."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM aggregations WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        out = []
        for r in rows:
            agg = _as_dict(r)
            agg["studies"] = _studies(conn, agg["aggregation_id"])
            out.append(agg)
    return out


def get_aggregation(aggregation_id: str, user_id: str) -> Optional[dict]:
    with _conn() as conn:
        return _get(conn, aggregation_id, user_id)


def rename_aggregation(aggregation_id: str, user_id: str, name: str) -> Optional[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE aggregations SET name=?, updated_at=? WHERE aggregation_id=? AND user_id=?",
            (name, _now(), aggregation_id, user_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        return _get(conn, aggregation_id, user_id)


def delete_aggregation(aggregation_id: str, user_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM aggregations WHERE aggregation_id=? AND user_id=?",
            (aggregation_id, user_id),
        )
        conn.commit()
    return cur.rowcount > 0


def add_study_to_aggregation(aggregation_id: str, user_id: str, study: dict,
                             fastq_artifact_count: int) -> Optional[dict]:
    """Returns the full aggregation, or None if it doesn't exist / isn't owned
    by user_id. The study cap is enforced by the route."""
    now = _now()
    with _conn() as conn:
        if not _owned(conn, aggregation_id, user_id):
            return None
        conn.execute(
            """INSERT OR IGNORE INTO aggregation_studies
               (aggregation_id, study_id, study_title, data_types, num_samples, num_preps,
                fastq_artifact_count, added_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (aggregation_id, int(study["study_id"]), study.get("study_title"),
             study.get("data_types"), study.get("num_samples"), study.get("num_preps"),
             fastq_artifact_count, now),
        )
        _touch(conn, aggregation_id, now)
        conn.commit()
        return _get(conn, aggregation_id, user_id)


def remove_study_from_aggregation(aggregation_id: str, user_id: str, study_id: int) -> Optional[dict]:
    """Returns the full aggregation, or None if it doesn't exist / isn't owned by user_id."""
    now = _now()
    with _conn() as conn:
        if not _owned(conn, aggregation_id, user_id):
            return None
        conn.execute(
            "DELETE FROM aggregation_studies WHERE aggregation_id=? AND study_id=?",
            (aggregation_id, int(study_id)),
        )
        _touch(conn, aggregation_id, now)
        conn.commit()
        return _get(conn, aggregation_id, user_id)
