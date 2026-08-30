"""Persistence surface for the chat-turn machinery, split out so crud.py /
global_chat_crud.py stay under the 500-line cap.

Phase 0 ships the lean history loaders: role/content-only projections for the
chat-stream routes. Neither loader touches ui_payload, whose per-message JSON
decode is the entire cost of the full load (measured 12.2MB / 13M chars on a
real chat's largest transcript). Later phases add the split user/assistant
persist and compaction-state accessors here.
"""
from .db import _conn


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
