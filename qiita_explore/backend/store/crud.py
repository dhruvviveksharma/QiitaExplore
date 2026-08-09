"""Project, study, and chat CRUD built on top of the store.db core."""

import json
import uuid

from .db import _conn, _as_dict, _now, _resolve_user, _chat_title


def get_setting(key: str):
    with _conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def _project_exists(conn, project_id, user_id):
    row = conn.execute(
        "SELECT project_id FROM projects WHERE project_id = ? AND user_id = ?",
        (project_id, user_id),
    ).fetchone()
    return row is not None


def list_projects(user_id: str, limit: int = 100):
    user_id = _resolve_user(user_id)
    limit = max(1, min(500, int(limit)))
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT p.project_id, p.name, p.created_at, p.updated_at,
                   (SELECT COUNT(1) FROM project_studies ps WHERE ps.project_id = p.project_id) AS studies_count,
                   (SELECT COUNT(1) FROM project_chats pc WHERE pc.project_id = p.project_id) AS chats_count
            FROM projects p
            WHERE p.user_id = ?
            ORDER BY p.updated_at DESC, p.created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [_as_dict(r) for r in rows]


def _load_project_studies(conn, project_id):
    rows = conn.execute(
        """
        SELECT study_id, study_title, study_abstract, pi_name, pi_email, pi_affiliation,
               lab_person_name, summary_text, data_types, num_samples, num_preps, preps_json,
               added_at, updated_at
        FROM project_studies
        WHERE project_id = ?
        ORDER BY added_at DESC, study_id ASC
        """,
        (project_id,),
    ).fetchall()
    return [_as_dict(r) for r in rows]


def _decode_ui(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _load_project_chat_messages(conn, chat_id):
    rows = conn.execute(
        """
        SELECT role, content, ui_payload, created_at
        FROM project_chat_messages
        WHERE chat_id = ?
        ORDER BY id ASC
        """,
        (chat_id,),
    ).fetchall()
    return [{**_as_dict(r), "ui_payload": _decode_ui(r["ui_payload"])} for r in rows]


def _load_project_chats(conn, project_id):
    rows = conn.execute(
        """
        SELECT chat_id, title, created_at, updated_at
        FROM project_chats
        WHERE project_id = ?
        ORDER BY updated_at DESC, created_at DESC
        """,
        (project_id,),
    ).fetchall()
    if not rows:
        return []
    chat_ids = [r["chat_id"] for r in rows]
    placeholders = ",".join("?" * len(chat_ids))
    msg_rows = conn.execute(
        f"SELECT chat_id, role, content, ui_payload, created_at FROM project_chat_messages "
        f"WHERE chat_id IN ({placeholders}) ORDER BY id ASC",
        chat_ids,
    ).fetchall()
    messages_by_chat = {}
    for msg in msg_rows:
        messages_by_chat.setdefault(msg["chat_id"], []).append(
            {**_as_dict(msg), "ui_payload": _decode_ui(msg["ui_payload"])}
        )
    return [
        {**_as_dict(row), "messages": messages_by_chat.get(row["chat_id"], [])}
        for row in rows
    ]


def create_project(user_id: str, name: str):
    user_id = _resolve_user(user_id)
    name = (name or "Untitled").strip() or "Untitled"
    project_id = str(uuid.uuid4())[:8]
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO projects(project_id, user_id, name, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (project_id, user_id, name, now, now),
        )
        conn.commit()
    return get_project(project_id, user_id)


def get_project(project_id: str, user_id: str):
    """Return the project only if it is owned by user_id — no cross-user fallback."""
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT project_id, user_id, name, created_at, updated_at FROM projects WHERE project_id = ? AND user_id = ?",
            (project_id, resolved_user),
        ).fetchone()
        if row is None:
            return None
        project = _as_dict(row)
        project["studies"] = _load_project_studies(conn, project_id)
        project["chats"] = _load_project_chats(conn, project_id)
        return project


def update_project(project_id: str, user_id: str, name: str = None):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        if not _project_exists(conn, project_id, resolved_user):
            return None
        if name is None:
            conn.execute(
                "UPDATE projects SET updated_at = ? WHERE project_id = ? AND user_id = ?",
                (_now(), project_id, resolved_user),
            )
        else:
            clean_name = (name or "").strip() or "Untitled"
            conn.execute(
                "UPDATE projects SET name = ?, updated_at = ? WHERE project_id = ? AND user_id = ?",
                (clean_name, _now(), project_id, resolved_user),
            )
        conn.commit()
    return get_project(project_id, resolved_user)


def delete_project(project_id: str, user_id: str):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        conn.execute(
            "DELETE FROM projects WHERE project_id = ? AND user_id = ?",
            (project_id, resolved_user),
        )
        conn.commit()
    return True


def add_study_to_project(project_id: str, user_id: str, study: dict):
    resolved_user = _resolve_user(user_id)
    if not study or study.get("study_id") is None:
        return None
    with _conn() as conn:
        if not _project_exists(conn, project_id, resolved_user):
            return None
        now = _now()
        conn.execute(
            """
            INSERT OR IGNORE INTO project_studies(
                project_id, study_id, study_title, study_abstract, pi_name,
                pi_email, pi_affiliation, lab_person_name, summary_text,
                data_types, num_samples, num_preps, preps_json,
                added_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                int(study.get("study_id")),
                study.get("study_title") or "",
                study.get("study_abstract") or "",
                study.get("pi_name"),
                study.get("pi_email"),
                study.get("pi_affiliation"),
                study.get("lab_person_name"),
                study.get("summary_text"),
                study.get("data_types"),
                study.get("num_samples"),
                study.get("num_preps"),
                study.get("preps_json"),
                now,
                now,
            ),
        )
        conn.execute(
            "DELETE FROM project_context_summaries WHERE project_id = ?",
            (project_id,),
        )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = ? AND user_id = ?",
            (_now(), project_id, resolved_user),
        )
        conn.commit()
    return get_project(project_id, resolved_user)


def remove_study_from_project(project_id: str, user_id: str, study_id):
    resolved_user = _resolve_user(user_id)
    sid = int(study_id)
    with _conn() as conn:
        if not _project_exists(conn, project_id, resolved_user):
            return None
        conn.execute(
            "DELETE FROM project_studies WHERE project_id = ? AND study_id = ?",
            (project_id, sid),
        )
        conn.execute(
            """
            DELETE FROM chat_pinned_studies
            WHERE chat_scope = 'project' AND study_id = ?
              AND chat_id IN (SELECT chat_id FROM project_chats WHERE project_id = ?)
            """,
            (sid, project_id),
        )
        conn.execute(
            "DELETE FROM project_context_summaries WHERE project_id = ?",
            (project_id,),
        )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = ? AND user_id = ?",
            (_now(), project_id, resolved_user),
        )
        conn.commit()
    return get_project(project_id, resolved_user)


def get_project_studies_only(project_id: str):
    """Lightweight fetch — returns only the studies list, skipping chats and messages."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT project_id, user_id, name, created_at, updated_at FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        project = _as_dict(row)
        project["studies"] = _load_project_studies(conn, project_id)
        return project


def get_project_id_for_chat(chat_id: str):
    """Return the project_id for a project chat, or None if not found."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT project_id FROM project_chats WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return row["project_id"] if row else None


def allowed_project_study_ids(project_id: str) -> set:
    """Normalized set of study IDs currently in a project."""
    proj = get_project_studies_only(project_id) or {}
    return {
        int(s["study_id"])
        for s in (proj.get("studies") or [])
        if s.get("study_id") is not None
    }


def list_chats(project_id: str, user_id: str, limit: int = 200):
    resolved_user = _resolve_user(user_id)
    limit = max(1, min(500, int(limit)))
    with _conn() as conn:
        if not _project_exists(conn, project_id, resolved_user):
            return []
        rows = conn.execute(
            """
            SELECT pc.chat_id, pc.title, pc.created_at, pc.updated_at,
                   (SELECT COUNT(1) FROM project_chat_messages m WHERE m.chat_id = pc.chat_id) AS messages_count
            FROM project_chats pc
            WHERE pc.project_id = ? AND pc.user_id = ?
            ORDER BY pc.updated_at DESC, pc.created_at DESC
            LIMIT ?
            """,
            (project_id, resolved_user, limit),
        ).fetchall()
    return [_as_dict(r) for r in rows]


def project_chat_exists(project_id: str, user_id: str, chat_id: str) -> bool:
    """Ownership check without loading the chat — see global_chat_exists."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM project_chats WHERE project_id = ? AND user_id = ? AND chat_id = ? LIMIT 1",
            (project_id, _resolve_user(user_id), chat_id),
        ).fetchone()
    return row is not None


def get_chat(project_id: str, user_id: str, chat_id: str):
    from store.cache import SCOPE_PROJECT, _load_pinned_study_meta
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT chat_id, title, created_at, updated_at
            FROM project_chats
            WHERE project_id = ? AND user_id = ? AND chat_id = ?
            """,
            (project_id, resolved_user, chat_id),
        ).fetchone()
        if row is None:
            return None
        chat = _as_dict(row)
        chat["messages"] = _load_project_chat_messages(conn, chat_id)
        meta = _load_pinned_study_meta(conn, chat_id, SCOPE_PROJECT)
        chat["pinned_study_meta"] = meta
        chat["pinned_studies"] = [m["study_id"] for m in meta]
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM project_studies WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        chat["total_studies_in_project"] = int(total["c"]) if total else 0
        return chat


def create_chat(project_id: str, user_id: str, first_message: str = None):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        if not _project_exists(conn, project_id, resolved_user):
            return None
        chat_id = str(uuid.uuid4())[:8]
        title = _chat_title(first_message)
        now = _now()
        conn.execute(
            """
            INSERT INTO project_chats(chat_id, project_id, user_id, title, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (chat_id, project_id, resolved_user, title, now, now),
        )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = ? AND user_id = ?",
            (now, project_id, resolved_user),
        )
        conn.commit()
    return get_chat(project_id, resolved_user, chat_id)


def _insert_chat_message_pair(
    conn,
    messages_table: str,
    chat_id: str,
    user_content: str,
    assistant_content: str,
    assistant_ui_payload: dict,
    now: str,
):
    conn.execute(
        f"INSERT INTO {messages_table}(chat_id, role, content, created_at) VALUES(?, 'user', ?, ?)",
        (chat_id, user_content or "", now),
    )
    ui_json = json.dumps(assistant_ui_payload) if assistant_ui_payload else None
    conn.execute(
        f"INSERT INTO {messages_table}(chat_id, role, content, ui_payload, created_at) VALUES(?, 'assistant', ?, ?, ?)",
        (chat_id, assistant_content or "", ui_json, now),
    )


def _resolved_chat_title(existing_title: str, user_content: str) -> str:
    title = existing_title or "New chat"
    if title == "New chat":
        title = _chat_title(user_content)
    return title


def append_chat_messages(
    project_id: str,
    user_id: str,
    chat_id: str,
    user_content: str,
    assistant_content: str,
    assistant_ui_payload: dict = None,
):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        exists = conn.execute(
            "SELECT chat_id, title FROM project_chats WHERE project_id = ? AND user_id = ? AND chat_id = ?",
            (project_id, resolved_user, chat_id),
        ).fetchone()
        if exists is None:
            return None

        now = _now()
        _insert_chat_message_pair(
            conn, "project_chat_messages", chat_id,
            user_content, assistant_content, assistant_ui_payload, now,
        )
        title = _resolved_chat_title(exists["title"], user_content)
        conn.execute(
            "UPDATE project_chats SET title = ?, updated_at = ? WHERE chat_id = ?",
            (title, now, chat_id),
        )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = ? AND user_id = ?",
            (now, project_id, resolved_user),
        )
        conn.commit()

    return get_chat(project_id, resolved_user, chat_id)


def delete_chat(project_id: str, user_id: str, chat_id: str):
    resolved_user = _resolve_user(user_id)
    with _conn() as conn:
        conn.execute(
            "DELETE FROM project_chats WHERE project_id = ? AND user_id = ? AND chat_id = ?",
            (project_id, resolved_user, chat_id),
        )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = ? AND user_id = ?",
            (_now(), project_id, resolved_user),
        )
        conn.commit()
    return get_project(project_id, resolved_user)


# list_global_chats, get_global_chat, create_global_chat,
# append_global_chat_messages, delete_global_chat moved to
# store/global_chat_crud.py to keep this file under the 500-line cap.
