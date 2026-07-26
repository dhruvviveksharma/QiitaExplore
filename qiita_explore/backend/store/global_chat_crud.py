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
    """Full messages, ui_payload decoded — for hydrating a chat in the UI.

    Expensive: ui_payload is where samples_report payloads live, and they are
    ~147x the size of the message text (13.2 MB across 72 rows on barnacle, one
    chat holding 12.2 MB). Only call this when the caller actually renders
    messages. For history that feeds an LLM, use load_global_chat_history.
    """
    rows = conn.execute(
        "SELECT role, content, ui_payload, created_at FROM global_chat_messages WHERE chat_id = ? ORDER BY id ASC",
        (chat_id,),
    ).fetchall()
    return [{**_as_dict(r), "ui_payload": _decode_ui(r["ui_payload"])} for r in rows]


def get_pi_session_file(chat_id: str, scope: str):
    """The stored path of this chat's pi session JSONL, or None.

    Lets the sidecar reopen a transcript directly instead of scanning its session
    directory. Not user-scoped on purpose: it is read on a path where ownership
    has already been established by the caller, and adding a user_id predicate
    here would imply this is itself an authorization check, which it is not.
    """
    from store.cache import SCOPE_GLOBAL
    table = "global_chats" if scope == SCOPE_GLOBAL else "project_chats"
    with _conn() as conn:
        row = conn.execute(
            f"SELECT pi_session_file FROM {table} WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return row["pi_session_file"] if row else None


def set_pi_session_file(chat_id: str, scope: str, session_file: str):
    """Record the path the sidecar reports on first session creation.

    Write-once in practice: only set when currently NULL, so a stale value from
    a concurrent turn cannot clobber a live one.
    """
    from store.cache import SCOPE_GLOBAL
    if not session_file:
        return
    table = "global_chats" if scope == SCOPE_GLOBAL else "project_chats"
    with _conn() as conn:
        conn.execute(
            f"UPDATE {table} SET pi_session_file = ? WHERE chat_id = ? AND pi_session_file IS NULL",
            (session_file, chat_id),
        )
        conn.commit()


def load_global_chat_history(chat_id: str):
    """role/content only — what build_full_msgs needs and all it ever reads.

    Deliberately omits ui_payload from the projection rather than selecting and
    discarding it: that column is the entire cost of this query.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM global_chat_messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
    return [_as_dict(r) for r in rows]


def get_global_chat(user_id: str, chat_id: str, include_messages: bool = True):
    """include_messages=False for callers that need title/pinned_studies only.

    The streaming routes are the reason: they used the returned `chat` for
    `title` and `pinned_studies`, but the message load came along anyway and
    every turn paid for the whole transcript.
    """
    from store.cache import SCOPE_GLOBAL, _load_pinned_studies
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT chat_id, title, created_at, updated_at FROM global_chats WHERE user_id = ? AND chat_id = ?",
            (resolved_user, chat_id),
        ).fetchone()
        if row is None:
            return None
        chat = _as_dict(row)
        if include_messages:
            chat["messages"] = _load_global_messages(conn, chat_id)
        chat["pinned_studies"] = _load_pinned_studies(conn, chat_id, SCOPE_GLOBAL)
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
