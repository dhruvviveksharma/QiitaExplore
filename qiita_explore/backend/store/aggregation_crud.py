"""CRUD for aggregations / aggregation_studies / aggregation_samples — a user's
named set of samples, grouped by study, whose per-sample sequence files feed
one CSV export (helpers/fastq_manifest.fetch_aggregate_csv_rows).

Membership is per sample: adding a study inserts every one of its sample ids
(all checked); set_aggregation_samples edits that set. The study row is a
header snapshot (title, abstract, PI, year, GOLD, counts) so the tab renders
Browse-style cards without a Qiita round-trip.

Own module for the same reason as merge_crud.py: store/crud.py sits at the
500-line cap. Every mutator returns the full aggregation so the frontend can
patch its state from the response body instead of re-fetching.
"""

import json
import uuid
from typing import Optional

from .db import _conn, _as_dict, _now

AGGREGATION_STUDIES_CAP = 50

_STUDIES_SQL = """
SELECT s.*,
       (SELECT COUNT(*) FROM aggregation_samples x
         WHERE x.aggregation_id = s.aggregation_id AND x.study_id = s.study_id) AS selected_samples
FROM aggregation_studies s
WHERE s.aggregation_id = ?
ORDER BY s.added_at, s.study_id
"""
_INSERT_SAMPLE_SQL = (
    "INSERT OR IGNORE INTO aggregation_samples(aggregation_id, study_id, sample_id, added_at)"
    " VALUES(?,?,?,?)"
)


def _studies(conn, aggregation_id: str) -> list:
    return [_as_dict(r) for r in conn.execute(_STUDIES_SQL, (aggregation_id,)).fetchall()]


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


def _file_filter(raw) -> dict:
    """file_filter_json → {"data_types": [...], "processing": [...]}; a
    missing or unreadable value means no filter."""
    try:
        f = json.loads(raw) if raw else {}
    except ValueError:
        f = {}
    return {"data_types": list(f.get("data_types") or []), "processing": list(f.get("processing") or [])}


def _hydrate(conn, row) -> dict:
    agg = _as_dict(row)
    agg["file_filter"] = _file_filter(agg.pop("file_filter_json", None))
    agg["studies"] = _studies(conn, agg["aggregation_id"])
    return agg


def _get(conn, aggregation_id: str, user_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM aggregations WHERE aggregation_id=? AND user_id=?",
        (aggregation_id, user_id),
    ).fetchone()
    return None if row is None else _hydrate(conn, row)


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
            "created_at": now, "updated_at": now,
            "file_filter": _file_filter(None), "studies": []}


def list_aggregations(user_id: str) -> list:
    """Every aggregation of user_id, each with its studies embedded."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM aggregations WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [_hydrate(conn, r) for r in rows]


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


def set_aggregation_file_filter(aggregation_id: str, user_id: str, file_filter: dict) -> Optional[dict]:
    """Save the data-type / processing filter; None when not owned. The caller
    validates the shape (routes/aggregation_routes.py)."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE aggregations SET file_filter_json=?, updated_at=? WHERE aggregation_id=? AND user_id=?",
            (json.dumps(file_filter), _now(), aggregation_id, user_id),
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
                             fastq_artifact_count: int, sample_ids=()) -> Optional[dict]:
    """Add a study with every id in sample_ids checked. Returns the full
    aggregation, or None if it doesn't exist / isn't owned by user_id. The
    study cap is enforced by the route."""
    now = _now()
    sid = int(study["study_id"])
    with _conn() as conn:
        if not _owned(conn, aggregation_id, user_id):
            return None
        cur = conn.execute(
            """INSERT OR IGNORE INTO aggregation_studies
               (aggregation_id, study_id, study_title, data_types, num_samples, num_preps,
                fastq_artifact_count, study_abstract, pi_name, pi_affiliation, year, is_gold,
                added_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aggregation_id, sid, study.get("study_title"), study.get("data_types"),
             study.get("num_samples"), study.get("num_preps"), fastq_artifact_count,
             study.get("study_abstract"), study.get("pi_name"), study.get("pi_affiliation"),
             study.get("year"), int(bool(study.get("is_gold"))), now),
        )
        # Only a freshly added study gets its samples checked: re-adding one
        # that is already here must not undo what the user unchecked.
        if cur.rowcount == 1 and sample_ids:
            conn.executemany(_INSERT_SAMPLE_SQL,
                             [(aggregation_id, sid, str(s), now) for s in sample_ids])
        _touch(conn, aggregation_id, now)
        conn.commit()
        return _get(conn, aggregation_id, user_id)


def remove_study_from_aggregation(aggregation_id: str, user_id: str, study_id: int) -> Optional[dict]:
    """Returns the full aggregation, or None if it doesn't exist / isn't owned
    by user_id. The study's sample rows go with it (FK cascade)."""
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


def set_aggregation_samples(aggregation_id: str, user_id: str, study_id: int, *,
                            add=(), remove=(), clear: bool = False) -> Optional[dict]:
    """Edit one study's checked samples: `clear` drops them all first (so
    clear + add = "exactly these"), then `add` / `remove` apply. Returns the
    full aggregation, or None if it doesn't exist / isn't owned by user_id.
    Ids are stored as given — a bogus id is counted but never resolves to a
    file in the CSV export."""
    now = _now()
    sid = int(study_id)
    with _conn() as conn:
        if not _owned(conn, aggregation_id, user_id):
            return None
        if clear:
            conn.execute("DELETE FROM aggregation_samples WHERE aggregation_id=? AND study_id=?",
                         (aggregation_id, sid))
        if add:
            conn.executemany(_INSERT_SAMPLE_SQL, [(aggregation_id, sid, str(s), now) for s in add])
        if remove:
            conn.executemany(
                "DELETE FROM aggregation_samples WHERE aggregation_id=? AND study_id=? AND sample_id=?",
                [(aggregation_id, sid, str(s)) for s in remove],
            )
        _touch(conn, aggregation_id, now)
        conn.commit()
        return _get(conn, aggregation_id, user_id)


def selected_in(aggregation_id: str, study_id: int, sample_ids) -> set:
    """The subset of sample_ids that are checked for this study — used to flag
    one page of the sample table. A single IN (...) bind list: callers pass at
    most one page, and the samples route clamps the page to 500, under
    SQLite's historical 999-variable ceiling."""
    ids = [str(s) for s in sample_ids]
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT sample_id FROM aggregation_samples"
            f" WHERE aggregation_id=? AND study_id=? AND sample_id IN ({marks})",
            (aggregation_id, int(study_id), *ids),
        ).fetchall()
    return {r[0] for r in rows}


def selected_by_study(aggregation_id: str) -> dict:
    """{study_id: {sample_id, ...}} — the CSV export's allowlist. No ownership
    check: callers have already resolved the aggregation via get_aggregation."""
    out = {}
    with _conn() as conn:
        rows = conn.execute(
            "SELECT study_id, sample_id FROM aggregation_samples WHERE aggregation_id=?",
            (aggregation_id,),
        ).fetchall()
    for r in rows:
        out.setdefault(int(r[0]), set()).add(r[1])
    return out
