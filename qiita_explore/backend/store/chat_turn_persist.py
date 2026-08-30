"""Persistence surface for the chat-turn machinery, split out so crud.py /
global_chat_crud.py stay under the 500-line cap.

Two pieces live here:
- Lean history loaders: role/content-only projections for the chat-stream
  routes. Neither loader touches ui_payload, whose per-message JSON decode is
  the entire cost of the full load (measured 12.2MB / 13M chars on a real
  chat's largest transcript).
- The split user/assistant persist for the agentic turn: the user row lands
  BEFORE the turn runs, and the assistant row lands on completion, error, or
  abort — so a failed or stopped turn no longer silently loses everything
  (previously not even the user's message survived). Duplicate user rows on a
  hard crash-and-resend are accepted deliberately (no idempotency key); an
  occasional duplicate bubble beats total loss.
"""
import json

from .db import _conn, _now, _resolve_user
from .crud import _resolved_chat_title

SCOPE_GLOBAL = "global"


def _tables(scope):
    if scope == SCOPE_GLOBAL:
        return "global_chats", "global_chat_messages"
    return "project_chats", "project_chat_messages"


def append_user_message(scope, chat_id, user_id, user_content, project_id=None):
    """Insert the user row and bump title/updated_at NOW, before the turn runs.
    Returns the new row id, or None if the chat isn't found/owned (same 404
    semantics the combined append had)."""
    chats_tbl, msgs_tbl = _tables(scope)
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        if scope == SCOPE_GLOBAL:
            row = conn.execute(
                f"SELECT title FROM {chats_tbl} WHERE user_id = ? AND chat_id = ?",
                (resolved_user, chat_id)).fetchone()
        else:
            row = conn.execute(
                f"SELECT title FROM {chats_tbl} WHERE project_id = ? AND user_id = ? AND chat_id = ?",
                (project_id, resolved_user, chat_id)).fetchone()
        if row is None:
            return None
        now = _now()
        cur = conn.execute(
            f"INSERT INTO {msgs_tbl}(chat_id, role, content, created_at) VALUES(?, 'user', ?, ?)",
            (chat_id, user_content or "", now))
        title = _resolved_chat_title(row["title"], user_content)
        conn.execute(f"UPDATE {chats_tbl} SET title = ?, updated_at = ? WHERE chat_id = ?",
                     (title, now, chat_id))
        if scope != SCOPE_GLOBAL and project_id:
            conn.execute("UPDATE projects SET updated_at = ? WHERE project_id = ? AND user_id = ?",
                         (now, project_id, resolved_user))
        conn.commit()
        return cur.lastrowid


def append_assistant_message(scope, chat_id, assistant_content, assistant_ui_payload=None):
    """Insert the assistant row — called on normal completion AND (with
    whatever partial content accumulated) from the abort/error handlers."""
    chats_tbl, msgs_tbl = _tables(scope)
    ui_json = json.dumps(assistant_ui_payload) if assistant_ui_payload else None
    with _conn() as conn:
        now = _now()
        conn.execute(
            f"INSERT INTO {msgs_tbl}(chat_id, role, content, ui_payload, created_at) "
            f"VALUES(?, 'assistant', ?, ?, ?)",
            (chat_id, assistant_content or "", ui_json, now))
        conn.execute(f"UPDATE {chats_tbl} SET updated_at = ? WHERE chat_id = ?", (now, chat_id))
        conn.commit()


def load_project_chat_history(chat_id: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM project_chat_messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def load_global_chat_history(chat_id: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM global_chat_messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]
