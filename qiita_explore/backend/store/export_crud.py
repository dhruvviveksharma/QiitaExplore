"""CRUD for chat_exports — the CSV / TSV files made from a chat
(helpers/export_tools.py; served by routes/export_routes.py). Only the owner reads
one; a chat's exports go with it (delete_chat_exports, called in the chat-delete
transactions next to delete_chat_aggregations) and move with it (chat_move.py).
"""

import json
import uuid

from .db import _conn, _now


def create_export(user_id: str, chat_id: str, chat_scope: str, name: str, source: str,
                  spec: dict, rows=None) -> str:
    export_id = str(uuid.uuid4())[:12]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO chat_exports(export_id, user_id, chat_id, chat_scope, name, source, "
            "spec_json, rows_json, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (export_id, user_id, chat_id, chat_scope, name, source, json.dumps(spec),
             json.dumps(rows) if rows is not None else None, _now()),
        )
        conn.commit()
    return export_id


def get_export(export_id: str, user_id: str):
    """{export_id, name, source, spec, rows} when user_id owns it, else None."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT export_id, name, source, spec_json, rows_json FROM chat_exports "
            "WHERE export_id = ? AND user_id = ?",
            (export_id, user_id),
        ).fetchone()
    if r is None:
        return None
    return {"export_id": r["export_id"], "name": r["name"], "source": r["source"],
            "spec": json.loads(r["spec_json"] or "{}"),
            "rows": json.loads(r["rows_json"]) if r["rows_json"] else None}


def delete_chat_exports(conn, chat_id: str, chat_scope: str) -> None:
    """Drop a chat's exports, inside the chat-delete transaction (after that
    delete matched the caller's own chat)."""
    conn.execute("DELETE FROM chat_exports WHERE chat_id = ? AND chat_scope = ?", (chat_id, chat_scope))
