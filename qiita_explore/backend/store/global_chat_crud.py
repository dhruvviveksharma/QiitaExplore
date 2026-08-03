"""CRUD for global_chats / global_chat_messages — split out of store/crud.py
to keep crud.py under the 500-line cap."""

import uuid

from .db import _conn, _as_dict, _now, _resolve_user, _chat_title
from .crud import _decode_ui, _insert_chat_message_pair, _resolved_chat_title


def list_global_chats(user_id: str, limit: int = 200):
    resolved_user = _resolve_user(user_id)
    limit = max(1, min(500, int(limit)))
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT gc.chat_id, gc.title, gc.created_at, gc.updated_at,
                   (SELECT COUNT(1) FROM global_chat_messages m WHERE m.chat_id = gc.chat_id) AS messages_count
            FROM global_chats gc
            WHERE gc.user_id = ?
            ORDER BY gc.updated_at DESC, gc.created_at DESC
            LIMIT ?
            """,
            (resolved_user, limit),
        ).fetchall()
    return [_as_dict(r) for r in rows]


def _load_global_messages(conn, chat_id):
    rows = conn.execute(
        "SELECT role, content, ui_payload, created_at FROM global_chat_messages WHERE chat_id = ? ORDER BY id ASC",
        (chat_id,),
    ).fetchall()
    return [{**_as_dict(r), "ui_payload": _decode_ui(r["ui_payload"])} for r in rows]


def get_global_chat(user_id: str, chat_id: str):
    from store.cache import SCOPE_GLOBAL, _load_pinned_studies, _load_pinned_study_meta
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT chat_id, title, created_at, updated_at FROM global_chats WHERE user_id = ? AND chat_id = ?",
            (resolved_user, chat_id),
        ).fetchone()
        if row is None:
            return None
        chat = _as_dict(row)
        chat["messages"] = _load_global_messages(conn, chat_id)
        chat["pinned_studies"] = _load_pinned_studies(conn, chat_id, SCOPE_GLOBAL)
        chat["pinned_study_meta"] = _load_pinned_study_meta(conn, chat_id, SCOPE_GLOBAL)
        return chat


def create_global_chat(user_id: str, title: str = None):
    resolved_user = _resolve_user(user_id)
    chat_id = str(uuid.uuid4())[:8]
    now = _now()
    resolved_title = _chat_title(title)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO global_chats(chat_id, user_id, title, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
            (chat_id, resolved_user, resolved_title, now, now),
        )
        conn.commit()
    return get_global_chat(resolved_user, chat_id)


def append_global_chat_messages(
    user_id: str,
    chat_id: str,
    user_content: str,
    assistant_content: str,
    assistant_ui_payload: dict = None,
):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT title FROM global_chats WHERE user_id = ? AND chat_id = ?",
            (resolved_user, chat_id),
        ).fetchone()
        if row is None:
            return None

        now = _now()
        _insert_chat_message_pair(
            conn, "global_chat_messages", chat_id,
            user_content, assistant_content, assistant_ui_payload, now,
        )
        title = _resolved_chat_title(row["title"], user_content)
        conn.execute(
            "UPDATE global_chats SET title = ?, updated_at = ? WHERE user_id = ? AND chat_id = ?",
            (title, now, resolved_user, chat_id),
        )
        conn.commit()

    return get_global_chat(resolved_user, chat_id)


def delete_global_chat(user_id: str, chat_id: str):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        conn.execute(
            "DELETE FROM global_chats WHERE user_id = ? AND chat_id = ?",
            (resolved_user, chat_id),
        )
        conn.commit()
    return {"ok": True}
