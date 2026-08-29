"""Move a chat between project scope and global scope, or between two
projects. Kept as its own module rather than growing crud.py (already at
the 500-line cap) — this is the one place that touches project_chats,
global_chats, both message tables, and chat_pinned_studies together.
"""

from .db import _conn, _now, _resolve_user
from .crud import _project_exists
from .cache import SCOPE_PROJECT, SCOPE_GLOBAL


def move_chat_to_project(user_id: str, chat_id: str, from_project_id: str, to_project_id: str):
    """Project -> Project. A single ownership-scoped UPDATE — the chat and its
    messages never move tables, so this is the cheap direction. Returns the
    moved chat's detail, or None if the chat or target project doesn't exist
    / isn't owned by user_id.

    Note: pinned-study visibility for this chat can silently change, since
    project-scope pin reads are filtered by the chat's *current* project's
    membership (_load_pinned_study_meta in cache.py) — a pinned study not in
    the destination project stops showing, even though its row survives.
    """
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        if not _project_exists(conn, to_project_id, resolved_user):
            return None
        cur = conn.execute(
            """
            UPDATE project_chats SET project_id = ?, updated_at = ?
            WHERE chat_id = ? AND project_id = ? AND user_id = ?
            """,
            (to_project_id, _now(), chat_id, from_project_id, resolved_user),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    from .crud import get_chat
    return get_chat(to_project_id, resolved_user, chat_id)


def move_global_chat_to_project(user_id: str, chat_id: str, target_project_id: str):
    """Global -> Project ("Move to project"). Returns the new project chat's
    detail, or None if the source chat or target project doesn't exist /
    isn't owned by user_id."""
    return _move_chat_between_scopes(
        user_id, chat_id,
        from_scope=SCOPE_GLOBAL, to_scope=SCOPE_PROJECT,
        target_project_id=target_project_id,
    )


def move_project_chat_to_global(user_id: str, chat_id: str, from_project_id: str):
    """Project -> Global ("Remove from project"). Mirror of the above."""
    return _move_chat_between_scopes(
        user_id, chat_id,
        from_scope=SCOPE_PROJECT, to_scope=SCOPE_GLOBAL,
        from_project_id=from_project_id,
    )


def _move_chat_between_scopes(
    user_id: str, chat_id: str, *, from_scope: str, to_scope: str,
    from_project_id: str = None, target_project_id: str = None,
):
    """One SQLite transaction: insert the destination chat row (same
    chat_id — it's just a UUID string PK in each table, no reason to mint a
    new one), copy every message across, re-point chat_pinned_studies'
    chat_scope, then delete the original chat + its messages. All four
    steps commit together or not at all.
    """
    resolved_user = _resolve_user(user_id)
    src_chats_tbl = "project_chats" if from_scope == SCOPE_PROJECT else "global_chats"
    src_msgs_tbl  = "project_chat_messages" if from_scope == SCOPE_PROJECT else "global_chat_messages"
    dst_msgs_tbl  = "project_chat_messages" if to_scope == SCOPE_PROJECT else "global_chat_messages"

    with _conn() as conn:
        if to_scope == SCOPE_PROJECT and not _project_exists(conn, target_project_id, resolved_user):
            return None

        if from_scope == SCOPE_PROJECT:
            row = conn.execute(
                """
                SELECT title, created_at, is_pinned, pinned_at, is_archived, archived_at
                FROM project_chats WHERE chat_id = ? AND project_id = ? AND user_id = ?
                """,
                (chat_id, from_project_id, resolved_user),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT title, created_at, is_pinned, pinned_at, is_archived, archived_at
                FROM global_chats WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, resolved_user),
            ).fetchone()
        if row is None:
            return None
        now = _now()

        if to_scope == SCOPE_PROJECT:
            conn.execute(
                """
                INSERT INTO project_chats(
                    chat_id, project_id, user_id, title, created_at, updated_at,
                    is_pinned, pinned_at, is_archived, archived_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (chat_id, target_project_id, resolved_user, row["title"], row["created_at"], now,
                 row["is_pinned"], row["pinned_at"], row["is_archived"], row["archived_at"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO global_chats(
                    chat_id, user_id, title, created_at, updated_at,
                    is_pinned, pinned_at, is_archived, archived_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (chat_id, resolved_user, row["title"], row["created_at"], now,
                 row["is_pinned"], row["pinned_at"], row["is_archived"], row["archived_at"]),
            )

        msg_rows = conn.execute(
            f"SELECT role, content, ui_payload, created_at FROM {src_msgs_tbl} "
            f"WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
        for m in msg_rows:
            conn.execute(
                f"INSERT INTO {dst_msgs_tbl}(chat_id, role, content, ui_payload, created_at) "
                f"VALUES(?, ?, ?, ?, ?)",
                (chat_id, m["role"], m["content"], m["ui_payload"], m["created_at"]),
            )

        conn.execute(
            "UPDATE chat_pinned_studies SET chat_scope = ? WHERE chat_id = ? AND chat_scope = ?",
            (to_scope, chat_id, from_scope),
        )

        conn.execute(f"DELETE FROM {src_msgs_tbl} WHERE chat_id = ?", (chat_id,))
        conn.execute(f"DELETE FROM {src_chats_tbl} WHERE chat_id = ?", (chat_id,))

        conn.commit()

    if to_scope == SCOPE_PROJECT:
        from .crud import get_chat
        return get_chat(target_project_id, resolved_user, chat_id)
    from .global_chat_crud import get_global_chat
    return get_global_chat(resolved_user, chat_id)
