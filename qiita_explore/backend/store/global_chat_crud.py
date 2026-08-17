"""CRUD for global_chats / global_chat_messages — split out of store/crud.py
to keep crud.py under the 500-line cap."""

import uuid

from .db import _conn, _as_dict, _now, _resolve_user, _chat_title
from .crud import _decode_ui, _insert_chat_message_pair, _resolved_chat_title


def list_global_chats(user_id: str, limit: int = 200, include_archived: bool = False):
    resolved_user = _resolve_user(user_id)
    limit = max(1, min(500, int(limit)))
    archived_clause = "" if include_archived else "AND COALESCE(gc.is_archived, 0) = 0"
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT gc.chat_id, gc.title, gc.created_at, gc.updated_at,
                   gc.is_pinned, gc.pinned_at, gc.is_archived, gc.archived_at,
                   (SELECT COUNT(1) FROM global_chat_messages m WHERE m.chat_id = gc.chat_id) AS messages_count
            FROM global_chats gc
            WHERE gc.user_id = ? {archived_clause}
            ORDER BY COALESCE(gc.is_pinned, 0) DESC, gc.pinned_at DESC, gc.updated_at DESC, gc.created_at DESC
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


def global_chat_exists(user_id: str, chat_id: str) -> bool:
    """Ownership check without loading the chat. get_global_chat decodes every
    message's ui_payload — for agentic turns that is tens of KB of segments — so
    callers that only need "does this exist and is it mine" use this instead."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM global_chats WHERE user_id = ? AND chat_id = ? LIMIT 1",
            (_resolve_user(user_id), chat_id),
        ).fetchone()
    return row is not None


def get_global_chat(user_id: str, chat_id: str):
    from store.cache import SCOPE_GLOBAL, _load_pinned_study_meta
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT chat_id, title, created_at, updated_at,
                   is_pinned, pinned_at, is_archived, archived_at
            FROM global_chats WHERE user_id = ? AND chat_id = ?
            """,
            (resolved_user, chat_id),
        ).fetchone()
        if row is None:
            return None
        chat = _as_dict(row)
        chat["messages"] = _load_global_messages(conn, chat_id)
        meta = _load_pinned_study_meta(conn, chat_id, SCOPE_GLOBAL)
        chat["pinned_study_meta"] = meta
        chat["pinned_studies"] = [m["study_id"] for m in meta]
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


def update_global_chat_title(user_id: str, chat_id: str, title: str):
    resolved_user = _resolve_user(user_id)
    clean = _chat_title(title)
    with _conn() as conn:
        cur = conn.execute(
            """
            UPDATE global_chats SET title = ?, updated_at = ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (clean, _now(), resolved_user, chat_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return {"chat_id": chat_id, "title": clean}


def set_global_chat_pinned(user_id: str, chat_id: str, pinned: bool):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        cur = conn.execute(
            """
            UPDATE global_chats SET is_pinned = ?, pinned_at = ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (1 if pinned else 0, _now() if pinned else None, resolved_user, chat_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return {"chat_id": chat_id, "is_pinned": bool(pinned)}


def set_global_chat_archived(user_id: str, chat_id: str, archived: bool):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        cur = conn.execute(
            """
            UPDATE global_chats SET is_archived = ?, archived_at = ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (1 if archived else 0, _now() if archived else None, resolved_user, chat_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return {"chat_id": chat_id, "is_archived": bool(archived)}


def delete_global_chat(user_id: str, chat_id: str):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        conn.execute(
            "DELETE FROM global_chats WHERE user_id = ? AND chat_id = ?",
            (resolved_user, chat_id),
        )
        conn.commit()
    return {"ok": True}
