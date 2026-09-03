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

from .db import _conn, _now, _resolve_user, _chat_title, UNTITLED

SCOPE_GLOBAL = "global"


def _tables(scope):
    if scope == SCOPE_GLOBAL:
        return "global_chats", "global_chat_messages"
    return "project_chats", "project_chat_messages"


def append_user_message(scope, chat_id, user_id, user_content, project_id=None):
    """Insert the user row and bump title/updated_at NOW, before the turn runs.
    Returns the new row id, or None if the chat isn't found/owned (same 404
    semantics the combined append had).

    The title write is an atomic conditional UPDATE, not a read-then-write:
    a background chat-title job (helpers/chat_title.py) may commit an
    LLM-generated title concurrently, and a stale read here would clobber it
    regardless of which of the two writes lands first."""
    chats_tbl, msgs_tbl = _tables(scope)
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        if scope == SCOPE_GLOBAL:
            row = conn.execute(
                f"SELECT 1 FROM {chats_tbl} WHERE user_id = ? AND chat_id = ?",
                (resolved_user, chat_id)).fetchone()
        else:
            row = conn.execute(
                f"SELECT 1 FROM {chats_tbl} WHERE project_id = ? AND user_id = ? AND chat_id = ?",
                (project_id, resolved_user, chat_id)).fetchone()
        if row is None:
            return None
        now = _now()
        cur = conn.execute(
            f"INSERT INTO {msgs_tbl}(chat_id, role, content, created_at) VALUES(?, 'user', ?, ?)",
            (chat_id, user_content or "", now))
        conn.execute(
            f"UPDATE {chats_tbl} SET title = CASE WHEN title = ? THEN ? ELSE title END, "
            f"updated_at = ? WHERE chat_id = ?",
            (UNTITLED, _chat_title(user_content), now, chat_id))
        if scope != SCOPE_GLOBAL and project_id:
            conn.execute("UPDATE projects SET updated_at = ? WHERE project_id = ? AND user_id = ?",
                         (now, project_id, resolved_user))
        conn.commit()
        return cur.lastrowid


def get_chat_title(chat_id, scope):
    """The chat's current stored title, or None if the chat doesn't exist."""
    chats_tbl, _ = _tables(scope)
    with _conn() as conn:
        row = conn.execute(f"SELECT title FROM {chats_tbl} WHERE chat_id = ?", (chat_id,)).fetchone()
    return row["title"] if row is not None else None


def set_auto_title(chat_id, scope, title, user_content):
    """Replace the provisional title with an LLM-generated one — but only if
    nothing else has claimed the row since: an explicit rename (any other
    string) wins over a late-arriving title-generation thread."""
    chats_tbl, _ = _tables(scope)
    provisional = _chat_title(user_content)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE {chats_tbl} SET title = ? WHERE chat_id = ? AND title IN (?, ?)",
            (title, chat_id, provisional, UNTITLED))
        conn.commit()
        return cur.rowcount == 1


def append_assistant_message(scope, chat_id, assistant_content, assistant_ui_payload=None,
                             model_transcript=None):
    """Insert the assistant row — called on normal completion AND (with
    whatever partial content accumulated) from the abort/error handlers.
    model_transcript is the normalized tool exchange for model replay
    (already truncated by helpers.chat_transcript.truncate_for_persist)."""
    chats_tbl, msgs_tbl = _tables(scope)
    ui_json = json.dumps(assistant_ui_payload) if assistant_ui_payload else None
    transcript_json = json.dumps(model_transcript) if model_transcript else None
    with _conn() as conn:
        now = _now()
        conn.execute(
            f"INSERT INTO {msgs_tbl}(chat_id, role, content, ui_payload, model_transcript, created_at) "
            f"VALUES(?, 'assistant', ?, ?, ?, ?)",
            (chat_id, assistant_content or "", ui_json, transcript_json, now))
        conn.execute(f"UPDATE {chats_tbl} SET updated_at = ? WHERE chat_id = ?", (now, chat_id))
        conn.commit()


def get_compaction_state(chat_id, scope):
    """{'summary': str|None, 'through_id': int|None} — the rolling history
    summary and the message row id it covers through."""
    chats_tbl, _ = _tables(scope)
    with _conn() as conn:
        row = conn.execute(
            f"SELECT compaction_summary, compacted_through_id FROM {chats_tbl} WHERE chat_id = ?",
            (chat_id,)).fetchone()
    if row is None:
        return {"summary": None, "through_id": None}
    return {"summary": row["compaction_summary"], "through_id": row["compacted_through_id"]}


def persist_compaction_state(chat_id, scope, *, summary, through_id):
    chats_tbl, _ = _tables(scope)
    with _conn() as conn:
        conn.execute(
            f"UPDATE {chats_tbl} SET compaction_summary = ?, compacted_through_id = ? "
            f"WHERE chat_id = ?",
            (summary, through_id, chat_id))
        conn.commit()


def load_turn_rows(chat_id, scope, since_id=None, until_id=None):
    """Rows for model replay: role/content plus the decoded model_transcript.
    Never touches ui_payload. since_id (exclusive) supports compaction
    anchoring; until_id (exclusive) excludes the just-appended user row."""
    _, msgs_tbl = _tables(scope)
    where = "chat_id = ?"
    params = [chat_id]
    if since_id is not None:
        where += " AND id > ?"
        params.append(since_id)
    if until_id is not None:
        where += " AND id < ?"
        params.append(until_id)
    params = tuple(params)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, role, content, model_transcript FROM {msgs_tbl} "
            f"WHERE {where} ORDER BY id ASC", params).fetchall()
    out = []
    for r in rows:
        transcript = None
        if r["model_transcript"]:
            try:
                transcript = json.loads(r["model_transcript"])
            except (ValueError, TypeError):
                transcript = None
        out.append({"id": r["id"], "role": r["role"], "content": r["content"],
                    "model_transcript": transcript})
    return out


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
