"""CRUD for aggregations / aggregation_studies / aggregation_files — a user's
named set of file rows, grouped by study, that feed one export
(helpers/fastq_manifest.fetch_export_rows).

Selection is per row: a row is a (sample_id, artifact_id) pair (the artifact fixes
its prep). Adding a study inserts every one of its rows (all checked);
set_aggregation_rows edits that set. Studies from before per-row selection keep
their checked samples in the legacy aggregation_samples table until
migrate_study_rows converts them. The study row is a
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
       CASE WHEN COALESCE(s.rows_v, 0) = 1
            THEN (SELECT COUNT(*) FROM aggregation_files x
                   WHERE x.aggregation_id = s.aggregation_id AND x.study_id = s.study_id)
            ELSE (SELECT COUNT(*) FROM aggregation_samples x
                   WHERE x.aggregation_id = s.aggregation_id AND x.study_id = s.study_id)
       END AS selected_rows
FROM aggregation_studies s
WHERE s.aggregation_id = ?
ORDER BY s.added_at, s.study_id
"""
_INSERT_ROW_SQL = (
    "INSERT OR IGNORE INTO aggregation_files(aggregation_id, study_id, sample_id, artifact_id, added_at)"
    " VALUES(?,?,?,?,?)"
)


def _studies(conn, aggregation_id: str) -> list:
    """The aggregation's studies, each with its own saved `file_filter`."""
    out = []
    for r in conn.execute(_STUDIES_SQL, (aggregation_id,)).fetchall():
        s = _as_dict(r)
        s["file_filter"] = _file_filter(s.pop("file_filter_json", None))
        out.append(s)
    return out


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
    """file_filter_json → {"data_types": [...], "processing": [...], "artifacts": [...]}; a
    missing or unreadable value means no filter."""
    try:
        f = json.loads(raw) if raw else {}
    except ValueError:
        f = {}
    return {"data_types": list(f.get("data_types") or []), "processing": list(f.get("processing") or []),
            "artifacts": list(f.get("artifacts") or [])}


def _hydrate(conn, row) -> dict:
    agg = _as_dict(row)
    agg.pop("file_filter_json", None)   # unused since the filter moved to each study
    agg["studies"] = _studies(conn, agg["aggregation_id"])
    return agg


def _get(conn, aggregation_id: str, user_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM aggregations WHERE aggregation_id=? AND user_id=?",
        (aggregation_id, user_id),
    ).fetchone()
    return None if row is None else _hydrate(conn, row)


def create_aggregation(user_id: str, name: str, chat_id: str = None, chat_scope: str = None) -> dict:
    """A saved aggregation, or — with chat_id / chat_scope — that chat's
    temporary one (see get_chat_aggregation)."""
    aggregation_id = str(uuid.uuid4())[:12]
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO aggregations(aggregation_id, user_id, name, created_at, updated_at, chat_id, chat_scope)"
            " VALUES(?,?,?,?,?,?,?)",
            (aggregation_id, user_id, name, now, now, chat_id, chat_scope),
        )
        conn.commit()
    return {"aggregation_id": aggregation_id, "user_id": user_id, "name": name,
            "created_at": now, "updated_at": now, "chat_id": chat_id, "chat_scope": chat_scope,
            "studies": []}


def get_chat_aggregation(user_id: str, chat_id: str, chat_scope: str) -> Optional[dict]:
    """The chat's temporary aggregation, or None. It is an ordinary aggregation
    with chat_id / chat_scope set: hidden from the Sample Aggregation tab,
    deleted with its chat (delete_chat_aggregations), kept by
    save_chat_aggregation."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM aggregations WHERE user_id=? AND chat_id=? AND chat_scope=?",
            (user_id, chat_id, chat_scope),
        ).fetchone()
        return None if row is None else _hydrate(conn, row)


def save_chat_aggregation(aggregation_id: str, user_id: str, name: str) -> Optional[dict]:
    """Name a chat's temporary aggregation and detach it from the chat, which
    makes it a saved one. None when it doesn't exist / isn't owned."""
    now = _now()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE aggregations SET name=?, chat_id=NULL, chat_scope=NULL, updated_at=?"
            " WHERE aggregation_id=? AND user_id=?",
            (name, now, aggregation_id, user_id),
        )
        if cur.rowcount == 0:
            return None
        conn.commit()
        return _get(conn, aggregation_id, user_id)


def delete_chat_aggregations(conn, chat_id: str, chat_scope: str) -> None:
    """Drop a chat's temporary aggregation (studies and rows cascade). Called
    inside the chat-delete transaction, only after that delete matched the
    caller's own chat."""
    conn.execute("DELETE FROM aggregations WHERE chat_id=? AND chat_scope=?", (chat_id, chat_scope))


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


def set_study_file_filter(aggregation_id: str, user_id: str, study_id: int,
                          file_filter: dict) -> Optional[dict]:
    """Save one study's data-type / processing / artifact filter. Returns the
    full aggregation, or None when the aggregation isn't owned by user_id or
    the study isn't in it. The caller validates the shape
    (routes/aggregation_routes.py)."""
    now = _now()
    with _conn() as conn:
        if not _owned(conn, aggregation_id, user_id):
            return None
        cur = conn.execute(
            "UPDATE aggregation_studies SET file_filter_json=? WHERE aggregation_id=? AND study_id=?",
            (json.dumps(file_filter), aggregation_id, int(study_id)),
        )
        if cur.rowcount == 0:
            return None
        _touch(conn, aggregation_id, now)
        conn.commit()
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
                             fastq_artifact_count: int, rows=(), file_filter=None) -> Optional[dict]:
    """Add a study with every (sample_id, artifact_id) in rows checked and, when
    given, its file_filter saved in the same insert. Returns the full
    aggregation, or None if it doesn't exist / isn't owned by user_id. The
    study cap is enforced by the route."""
    now = _now()
    sid = int(study["study_id"])
    rows = list(rows)
    with _conn() as conn:
        if not _owned(conn, aggregation_id, user_id):
            return None
        cur = conn.execute(
            """INSERT OR IGNORE INTO aggregation_studies
               (aggregation_id, study_id, study_title, data_types, num_samples, num_preps,
                fastq_artifact_count, study_abstract, pi_name, pi_affiliation, year, is_gold,
                added_at, rows_v, file_rows, file_filter_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (aggregation_id, sid, study.get("study_title"), study.get("data_types"),
             study.get("num_samples"), study.get("num_preps"), fastq_artifact_count,
             study.get("study_abstract"), study.get("pi_name"), study.get("pi_affiliation"),
             study.get("year"), int(bool(study.get("is_gold"))), now, len(rows),
             json.dumps(file_filter) if file_filter else None),
        )
        # Only a freshly added study gets its rows checked: re-adding one
        # that is already here must not undo what the user unchecked.
        if cur.rowcount == 1 and rows:
            conn.executemany(_INSERT_ROW_SQL,
                             [(aggregation_id, sid, str(s), int(a), now) for s, a in rows])
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


def set_aggregation_rows(aggregation_id: str, user_id: str, study_id: int, *,
                         add=(), remove=(), clear: bool = False) -> Optional[dict]:
    """Edit one study's checked rows ((sample_id, artifact_id) pairs): `clear`
    drops them all first (so clear + add = "exactly these"), then `add` /
    `remove` apply. Returns the full aggregation, or None if it doesn't exist /
    isn't owned by user_id. Pairs are stored as given — a bogus one is counted
    but never resolves to a file in the export."""
    now = _now()
    sid = int(study_id)
    with _conn() as conn:
        if not _owned(conn, aggregation_id, user_id):
            return None
        if clear:
            conn.execute("DELETE FROM aggregation_files WHERE aggregation_id=? AND study_id=?",
                         (aggregation_id, sid))
        if add:
            conn.executemany(_INSERT_ROW_SQL, [(aggregation_id, sid, str(s), int(a), now) for s, a in add])
        if remove:
            conn.executemany(
                "DELETE FROM aggregation_files WHERE aggregation_id=? AND study_id=? AND sample_id=? AND artifact_id=?",
                [(aggregation_id, sid, str(s), int(a)) for s, a in remove],
            )
        _touch(conn, aggregation_id, now)
        conn.commit()
        return _get(conn, aggregation_id, user_id)


def remove_rows_by_artifacts(aggregation_id: str, user_id: str, study_id: int, artifact_ids,
                             keep: bool = False) -> Optional[dict]:
    """Drop one study's checked rows whose artifact is in artifact_ids — or,
    with keep, every row whose artifact is NOT in them (the undo of a chat
    aggregation add). Returns the full aggregation, or None if not owned."""
    now = _now()
    sid = int(study_id)
    ids = [int(a) for a in artifact_ids]
    with _conn() as conn:
        if not _owned(conn, aggregation_id, user_id):
            return None
        marks = ",".join("?" * len(ids))
        if keep:
            where = f" AND artifact_id NOT IN ({marks})" if ids else ""
        elif ids:
            where = f" AND artifact_id IN ({marks})"
        else:
            where = None
        if where is not None:
            conn.execute(f"DELETE FROM aggregation_files WHERE aggregation_id=? AND study_id=?{where}",
                         (aggregation_id, sid, *ids))
        _touch(conn, aggregation_id, now)
        conn.commit()
        return _get(conn, aggregation_id, user_id)


def migrate_study_rows(aggregation_id: str, study_id: int, sample_files) -> bool:
    """Lazy, idempotent move of a pre-per-row study (rows_v = 0) to per-row
    selection: each of its checked samples becomes one row per artifact the
    sample has (sample_files = helpers.sample_files.get_sample_files' map),
    file_rows is snapshotted, and the legacy rows are dropped. True when it
    migrated, False when there was nothing to do. No ownership check: callers
    have already resolved the aggregation."""
    sid = int(study_id)
    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(rows_v, 0) FROM aggregation_studies WHERE aggregation_id=? AND study_id=?",
            (aggregation_id, sid),
        ).fetchone()
        if row is None or row[0] == 1:
            return False
        legacy = [r[0] for r in conn.execute(
            "SELECT sample_id FROM aggregation_samples WHERE aggregation_id=? AND study_id=?",
            (aggregation_id, sid)).fetchall()]
        conn.executemany(_INSERT_ROW_SQL, [(aggregation_id, sid, s, e[2], now)
                                           for s in legacy for e in sample_files.get(s, [])])
        conn.execute(
            "UPDATE aggregation_studies SET rows_v=1, file_rows=? WHERE aggregation_id=? AND study_id=?",
            (sum(len(v) for v in sample_files.values()), aggregation_id, sid),
        )
        conn.execute("DELETE FROM aggregation_samples WHERE aggregation_id=? AND study_id=?",
                     (aggregation_id, sid))
        conn.commit()
    return True


def selected_rows_in(aggregation_id: str, study_id: int, sample_ids) -> set:
    """The checked (sample_id, artifact_id) rows among these samples' — used to
    flag one page of the sample table. A single IN (...) bind list: callers pass
    at most one page's samples, and the samples route clamps the page to 500
    rows, under SQLite's historical 999-variable ceiling."""
    ids = [str(s) for s in sample_ids]
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT sample_id, artifact_id FROM aggregation_files"
            f" WHERE aggregation_id=? AND study_id=? AND sample_id IN ({marks})",
            (aggregation_id, int(study_id), *ids),
        ).fetchall()
    return {(r[0], r[1]) for r in rows}


def selected_by_study(aggregation_id: str) -> dict:
    """{study_id: {(sample_id, artifact_id), ...}} — the export's allowlist. No
    ownership check: callers have already resolved the aggregation via
    get_aggregation."""
    out = {}
    with _conn() as conn:
        rows = conn.execute(
            "SELECT study_id, sample_id, artifact_id FROM aggregation_files WHERE aggregation_id=?",
            (aggregation_id,),
        ).fetchall()
    for r in rows:
        out.setdefault(int(r[0]), set()).add((r[1], r[2]))
    return out
