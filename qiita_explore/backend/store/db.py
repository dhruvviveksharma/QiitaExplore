"""SQLite schema creation, migration helpers, and core connection utilities."""

import os
import sqlite3
from datetime import datetime

# Go up one level (store/ → backend/) so the data dir stays at backend/data/
_DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(_DEFAULT_DATA_DIR, exist_ok=True)
DB_PATH = os.getenv("QIITA_EXPERIMENT_DB_PATH", os.path.join(_DEFAULT_DATA_DIR, "projects.db"))

# WAL needs reliable POSIX locking + shared memory. /ddn_scratch is a network
# filesystem where that fails under multi-worker contention (four gunicorn
# workers racing PRAGMA journal_mode=WAL → "database is locked"). DELETE mode
# is the SQLite-recommended fallback for networked paths.
_ON_NETWORK_FS = DB_PATH.startswith("/ddn_scratch/")
_JOURNAL_MODE = "DELETE" if _ON_NETWORK_FS else "WAL"
# Wait up to 30s for a lock instead of Python's 5s default (docs/09-operations.md).
_BUSY_TIMEOUT_MS = 30000


def _now():
    return datetime.utcnow().isoformat() + "Z"


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA journal_mode = {_JOURNAL_MODE}")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _as_dict(row):
    return dict(row) if row is not None else None


def _resolve_user(user_id):
    return (user_id or "").strip() or "default"


def _chat_title(raw):
    return (raw or "New chat")[:60].strip() or "New chat"


def _reconcile_legacy_users_table(conn):
    """Migrate a stale pre-auth `users` table left behind by an older,
    pre-authentication version of this app.

    That old version's `users` table had an unrelated shape (username +
    password_hash based, no `principal_idx`). `CREATE TABLE IF NOT EXISTS`
    in the executescript below can't upgrade it in place — it just leaves
    the stale table untouched. The current auth code
    (store/auth_store.py:upsert_user) inserts rows keyed by `principal_idx`,
    a column the legacy table lacks, so it fails with
    `sqlite3.OperationalError: table users has no column named
    principal_idx` on every POST /api/auth/connect. Simply ALTERing columns
    in wouldn't fix it either: the legacy `username`/`password_hash` columns
    are NOT NULL and would reject the new principal-keyed INSERTs.

    The legacy rows are dead fake seed data from the old app — no real
    session ever referenced them, since upsert_user always failed before a
    session could be created. We preserve them non-destructively by
    renaming the table aside to `users_legacy_pre_auth` (rather than
    dropping it) so the executescript below can then CREATE the correct
    `users`/`auth_sessions` tables fresh.
    """
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if existing is None:
        return  # fresh DB; the executescript's CREATE TABLE handles it

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "principal_idx" in cols:
        return  # already current schema; no-op on every normal boot

    # Legacy shape detected. auth_sessions FKs to users(user_id); a legacy
    # users table means upsert_user never succeeded, so no session was ever
    # created. Guard defensively anyway before touching anything.
    sessions_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_sessions'"
    ).fetchone()
    if sessions_table is not None:
        count = conn.execute("SELECT COUNT(1) AS c FROM auth_sessions").fetchone()["c"]
        if count > 0:
            return  # impossible in practice, but leave it to a human if it happens

    conn.execute("DROP TABLE IF EXISTS auth_sessions")
    conn.execute("DROP TABLE IF EXISTS users_legacy_pre_auth")
    conn.execute("ALTER TABLE users RENAME TO users_legacy_pre_auth")


def _create_schema(conn):
    _reconcile_legacy_users_table(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS project_studies (
            project_id TEXT NOT NULL,
            study_id INTEGER NOT NULL,
            study_title TEXT,
            study_abstract TEXT,
            pi_name TEXT,
            pi_email TEXT,
            pi_affiliation TEXT,
            lab_person_name TEXT,
            summary_text TEXT,
            added_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (project_id, study_id),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS project_chats (
            chat_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            title TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS project_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (chat_id) REFERENCES project_chats(chat_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS global_chats (
            chat_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS global_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (chat_id) REFERENCES global_chats(chat_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS study_detail_cache (
            study_id INTEGER PRIMARY KEY,
            preps_json TEXT,
            artifacts_json TEXT,
            samples_context TEXT,
            cached_at TEXT
        );

        CREATE TABLE IF NOT EXISTS chat_pinned_studies (
            chat_id TEXT NOT NULL,
            chat_scope TEXT NOT NULL,
            study_id INTEGER NOT NULL,
            pinned_at TEXT,
            PRIMARY KEY (chat_id, chat_scope, study_id)
        );

        CREATE INDEX IF NOT EXISTS idx_projects_user_updated ON projects(user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_project_studies_project ON project_studies(project_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_project_studies_study ON project_studies(study_id);
        CREATE INDEX IF NOT EXISTS idx_project_chats_project_updated ON project_chats(project_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_global_chats_user_updated ON global_chats(user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_chat_pins ON chat_pinned_studies(chat_id, chat_scope);

        CREATE TABLE IF NOT EXISTS merge_workspaces (
            workspace_id TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            name         TEXT NOT NULL,
            created_at   TEXT,
            updated_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_merge_ws_user ON merge_workspaces(user_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS merge_workspace_studies (
            workspace_id       TEXT NOT NULL,
            study_id           INTEGER NOT NULL,
            study_title        TEXT,
            data_types         TEXT,
            num_samples        INTEGER,
            chosen_artifact_id INTEGER,
            sample_filter      TEXT,
            added_at           TEXT,
            PRIMARY KEY (workspace_id, study_id),
            FOREIGN KEY (workspace_id) REFERENCES merge_workspaces(workspace_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS merge_jobs (
            job_id         TEXT PRIMARY KEY,
            workspace_id   TEXT,
            user_id        TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'pending',
            error_message  TEXT,
            result_path    TEXT,
            workspace_snap TEXT,
            created_at     TEXT,
            updated_at     TEXT,
            FOREIGN KEY (workspace_id) REFERENCES merge_workspaces(workspace_id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_merge_jobs_ws ON merge_jobs(workspace_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_merge_jobs_user ON merge_jobs(user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS biom_sample_cache (
            artifact_id     INTEGER PRIMARY KEY,
            num_samples     INTEGER,
            sample_ids_json TEXT,
            cached_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            user_id          TEXT PRIMARY KEY,
            principal_idx    INTEGER NOT NULL,
            email            TEXT,
            system_role      TEXT,
            scopes           TEXT,
            profile_complete INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT,
            updated_at       TEXT,
            last_login_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS auth_sessions (
            session_hash        TEXT PRIMARY KEY,
            user_id             TEXT NOT NULL,
            pat_encrypted       TEXT NOT NULL,
            token_idx           TEXT,
            source              TEXT NOT NULL DEFAULT 'paste',
            pat_expires_at      TEXT,
            csrf_token          TEXT NOT NULL,
            created_at          TEXT,
            last_seen_at        TEXT,
            last_verified_at    TEXT,
            absolute_expires_at TEXT NOT NULL,
            revoked_at          TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(absolute_expires_at);
        """
    )
    for tbl, col, definition in [
        ("project_studies", "data_types", "TEXT"),
        ("project_studies", "num_samples", "INTEGER"),
        ("project_studies", "num_preps", "INTEGER"),
        ("project_studies", "preps_json", "TEXT"),
        ("study_detail_cache", "full_samples_json", "TEXT"),
        ("study_detail_cache", "full_samples_limit", "INTEGER"),
        ("study_detail_cache", "artifact_graph_json", "TEXT"),
        ("study_detail_cache", "prep_metadata_json", "TEXT"),
        ("study_detail_cache", "samples_json", "TEXT"),
        ("study_detail_cache", "total_samples", "INTEGER"),
        ("merge_workspace_studies", "chosen_artifact_ids", "TEXT"),
        ("project_chat_messages", "ui_payload", "TEXT"),
        ("global_chat_messages", "ui_payload", "TEXT"),
        ("chat_pinned_studies", "study_title", "TEXT"),
        ("project_chats", "is_pinned", "INTEGER DEFAULT 0"),
        ("project_chats", "pinned_at", "TEXT"),
        ("project_chats", "is_archived", "INTEGER DEFAULT 0"),
        ("project_chats", "archived_at", "TEXT"),
        ("global_chats", "is_pinned", "INTEGER DEFAULT 0"),
        ("global_chats", "pinned_at", "TEXT"),
        ("global_chats", "is_archived", "INTEGER DEFAULT 0"),
        ("global_chats", "archived_at", "TEXT"),
        # Normalized per-turn tool exchange for model replay — NULL on rows
        # from before this column (degrades to display-text-only history).
        ("project_chat_messages", "model_transcript", "TEXT"),
        ("global_chat_messages", "model_transcript", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {definition}")
        except Exception:
            pass

    # PATs are verified once at login and never stored. Scrub any legacy
    # ciphertext left from earlier builds (idempotent).
    conn.execute(
        "UPDATE auth_sessions SET pat_encrypted = '' WHERE pat_encrypted != ''"
    )


def _bootstrap():
    with _conn() as conn:
        _create_schema(conn)
        conn.commit()


_bootstrap()
