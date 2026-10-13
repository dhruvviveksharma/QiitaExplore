"""SQLite schema creation, migration helpers, and core connection utilities."""

import json
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


UNTITLED = "New chat"


def _chat_title(raw):
    return (raw or UNTITLED)[:60].strip() or UNTITLED


def _reconcile_legacy_users_table(conn):
    """Move aside a `users` table left by the pre-authentication version of
    this app (username + password_hash columns, fake seed rows), so the
    executescript below can create the real one.

    The old shape is detected positively, by its own columns. Testing for the
    absence of a current column instead would mistake a later schema for it
    and throw that away (it did, before the classic-Qiita login: v1 of that
    plan dropped `principal_idx`). The rows are kept, renamed to
    `users_legacy_pre_auth`; no session ever referenced them.
    """
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if existing is None:
        return  # fresh DB; the executescript's CREATE TABLE handles it

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "username" not in cols and "password_hash" not in cols:
        return  # not the pre-auth table; no-op on every normal boot

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

        CREATE TABLE IF NOT EXISTS aggregations (
            aggregation_id TEXT PRIMARY KEY,
            user_id        TEXT NOT NULL,
            name           TEXT NOT NULL,
            created_at     TEXT,
            updated_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_aggregations_user ON aggregations(user_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS aggregation_studies (
            aggregation_id       TEXT NOT NULL,
            study_id             INTEGER NOT NULL,
            study_title          TEXT,
            data_types           TEXT,
            num_samples          INTEGER,
            num_preps            INTEGER,
            fastq_artifact_count INTEGER,
            added_at             TEXT,
            PRIMARY KEY (aggregation_id, study_id),
            FOREIGN KEY (aggregation_id) REFERENCES aggregations(aggregation_id) ON DELETE CASCADE
        );

        -- Legacy per-sample membership (pre-2026-09-30). Only read by
        -- store.aggregation_crud.migrate_study_rows, which turns a study's checked
        -- samples into aggregation_files rows the first time it is opened.
        CREATE TABLE IF NOT EXISTS aggregation_samples (
            aggregation_id TEXT    NOT NULL,
            study_id       INTEGER NOT NULL,
            sample_id      TEXT    NOT NULL,
            added_at       TEXT,
            PRIMARY KEY (aggregation_id, study_id, sample_id),
            FOREIGN KEY (aggregation_id, study_id)
                REFERENCES aggregation_studies(aggregation_id, study_id) ON DELETE CASCADE
        );

        -- Per-row selection: one row per checked (sample, artifact) file row; the
        -- artifact fixes its prep. The composite FK cascades in a chain
        -- (aggregation -> studies -> rows); the PK autoindex serves every lookup
        -- (per-study count, page IN (...), per-study delete).
        CREATE TABLE IF NOT EXISTS aggregation_files (
            aggregation_id TEXT    NOT NULL,
            study_id       INTEGER NOT NULL,
            sample_id      TEXT    NOT NULL,
            artifact_id    INTEGER NOT NULL,
            added_at       TEXT,
            PRIMARY KEY (aggregation_id, study_id, sample_id, artifact_id),
            FOREIGN KEY (aggregation_id, study_id)
                REFERENCES aggregation_studies(aggregation_id, study_id) ON DELETE CASCADE
        );

        -- CSV / TSV files made from a chat (helpers/export_tools.py). spec_json holds
        -- the parameters of a samples / files export (rebuilt at download, so always
        -- current); rows_json the table of a model-written "rows" export. Deleted
        -- with the chat (store/export_crud.delete_chat_exports).
        CREATE TABLE IF NOT EXISTS chat_exports (
            export_id  TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            chat_id    TEXT NOT NULL,
            chat_scope TEXT NOT NULL,
            name       TEXT NOT NULL,
            source     TEXT NOT NULL,
            spec_json  TEXT,
            rows_json  TEXT,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chat_exports_chat ON chat_exports(chat_id, chat_scope);

        CREATE TABLE IF NOT EXISTS biom_sample_cache (
            artifact_id     INTEGER PRIMARY KEY,
            num_samples     INTEGER,
            sample_ids_json TEXT,
            cached_at       TEXT
        );

        -- Per-sample sequence-file availability of one study (helpers/sample_files.py).
        -- Own table + own cached_at: it used to be study_detail_cache.sample_files_json,
        -- whose partial writes poisoned the preps/artifacts reads (TKT-086).
        CREATE TABLE IF NOT EXISTS study_sample_files_cache (
            study_id          INTEGER PRIMARY KEY,
            sample_files_json TEXT,
            cached_at         TEXT
        );

        -- One row per QiitaExplore account (store/auth_store.py). Accounts log in
        -- with classic Qiita; qiita_email (normalized) binds a row to its Qiita
        -- account. principal_idx / miint_email: the qiita-miint identity of
        -- accounts from before 2026-10, kept so scripts/bind_classic_accounts.py
        -- can show who they were; NULL for newer accounts.
        CREATE TABLE IF NOT EXISTS users (
            user_id       TEXT PRIMARY KEY,
            qiita_email   TEXT UNIQUE,
            user_level    TEXT,
            principal_idx INTEGER,
            miint_email   TEXT,
            created_at    TEXT,
            updated_at    TEXT,
            last_login_at TEXT
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
            password_fp         TEXT,
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
        # History compaction: rolling summary of turns up to (and including)
        # message row id compacted_through_id; NULL = never compacted.
        ("project_chats", "compaction_summary", "TEXT"),
        ("project_chats", "compacted_through_id", "INTEGER"),
        ("global_chats", "compaction_summary", "TEXT"),
        ("global_chats", "compacted_through_id", "INTEGER"),
        # The last request's size by part, for the composer's context bar
        # (helpers/context_usage.py); NULL until the chat's first agent turn.
        ("project_chats", "context_usage", "TEXT"),
        ("global_chats", "context_usage", "TEXT"),
        # Study-header snapshot so the Sample Aggregation tab can render
        # Browse-style cards without a Qiita round-trip.
        ("aggregation_studies", "study_abstract", "TEXT"),
        ("aggregation_studies", "pi_name", "TEXT"),
        ("aggregation_studies", "pi_affiliation", "TEXT"),
        ("aggregation_studies", "year", "INTEGER"),
        ("aggregation_studies", "is_gold", "INTEGER"),
        # Unused since 2026-09-30: the data-type / processing / artifact filter
        # moved to aggregation_studies.file_filter_json (one per study; see
        # _move_aggregation_filters_to_studies). Kept so old DBs migrate alike.
        ("aggregations", "file_filter_json", "TEXT"),
        # One study's saved filter ({"data_types": [...], "processing": [...],
        # "artifacts": [...]}; empty = any). Drives that study's sample table
        # and its rows in the export (routes/aggregation_routes.py).
        ("aggregation_studies", "file_filter_json", "TEXT"),
        # rows_v = 1: this study's selection lives in aggregation_files (0 = still the
        # legacy aggregation_samples, migrated lazily). file_rows: how many (sample,
        # artifact) rows the study had when snapshotted — the "K / N rows" denominator.
        ("aggregation_studies", "rows_v", "INTEGER DEFAULT 0"),
        ("aggregation_studies", "file_rows", "INTEGER"),
        # A chat's temporary aggregation (helpers/aggregation_tools.py): set while it
        # belongs to that chat, NULL once saved. Hidden from the Sample Aggregation
        # tab, deleted with its chat.
        ("aggregations", "chat_id", "TEXT"),
        ("aggregations", "chat_scope", "TEXT"),
        # Unused since 2026-09-24: the availability map moved to its own
        # study_sample_files_cache table (TKT-086). Kept so old DBs migrate alike.
        ("study_detail_cache", "sample_files_json", "TEXT"),
        # sha256 of the account's bcrypt hash at login: a password changed on
        # classic Qiita revokes the session (helpers/auth_middleware.py).
        ("auth_sessions", "password_fp", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {definition}")
        except Exception:
            pass

    conn.execute("CREATE INDEX IF NOT EXISTS idx_aggregations_chat ON aggregations(chat_id, chat_scope)")
    _move_aggregation_filters_to_studies(conn)

    # PATs are verified once at login and never stored. Scrub any legacy
    # ciphertext left from earlier builds (idempotent).
    conn.execute(
        "UPDATE auth_sessions SET pat_encrypted = '' WHERE pat_encrypted != ''"
    )


def _move_aggregation_filters_to_studies(conn):
    """One-time move (idempotent): a filter saved on an aggregation (the
    pre-2026-09-30 shape) is copied to each of its studies that has none yet,
    then cleared. Artifact picks are dropped: an aggregation-wide artifact pick
    is exactly what emptied the other studies' tables and exports, and which
    study owns an artifact would need Qiita Postgres. Once cleared, later
    boots (and studies added later) find nothing to copy."""
    rows = conn.execute(
        "SELECT aggregation_id, file_filter_json FROM aggregations WHERE file_filter_json IS NOT NULL"
    ).fetchall()
    for aggregation_id, raw in rows:
        try:
            f = json.loads(raw) or {}
        except ValueError:
            f = {}
        moved = json.dumps({"data_types": list(f.get("data_types") or []),
                            "processing": list(f.get("processing") or []), "artifacts": []})
        conn.execute(
            "UPDATE aggregation_studies SET file_filter_json=? WHERE aggregation_id=? AND file_filter_json IS NULL",
            (moved, aggregation_id),
        )
        conn.execute("UPDATE aggregations SET file_filter_json=NULL WHERE aggregation_id=?", (aggregation_id,))


_USERS_CLASSIC_COLUMNS = """
    user_id       TEXT PRIMARY KEY,
    qiita_email   TEXT UNIQUE,
    user_level    TEXT,
    principal_idx INTEGER,
    miint_email   TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    last_login_at TEXT
"""


def _users_is_classic(conn) -> bool:
    return "qiita_email" in {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}


def _migrate_users_to_classic():
    """One-time rebuild of a qiita-miint-era `users` table into the classic-Qiita
    shape (2026-10). Old rows keep their user_id (all their data hangs off it)
    and principal_idx; their qiita-miint email moves to miint_email, and
    qiita_email starts NULL: binding an old account to a classic-Qiita login is
    an explicit, operator-confirmed step (scripts/bind_classic_accounts.py),
    never automatic. Every live session is revoked (all are qiita-miint ones).

    Done the only safe way SQLite allows (verified in the 2026-10-12 review):
    its own autocommit connection; foreign keys turned off *before* the
    transaction (a PRAGMA inside one is a no-op, and DROP TABLE users would then
    cascade-delete auth_sessions); BEGIN IMMEDIATE so one gunicorn worker
    migrates and the rest find it done; build users_new, drop users, rename
    users_new (renaming the old table aside would rewrite auth_sessions' foreign
    key to point at it); foreign_key_check must come back empty. A crash before
    COMMIT leaves the old table untouched.
    """
    conn = sqlite3.connect(DB_PATH, timeout=_BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
    try:
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        if _users_is_classic(conn):
            return
        conn.execute("PRAGMA foreign_keys = OFF")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
            raise RuntimeError("could not turn foreign keys off for the users migration")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _users_is_classic(conn):           # another worker got here first
                conn.execute("ROLLBACK")
                return
            old_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
            pick = lambda c: c if c in old_cols else "NULL"   # noqa: E731
            rows = conn.execute(
                f"SELECT user_id, {pick('principal_idx')}, {pick('email')}, {pick('created_at')}, "
                f"{pick('updated_at')}, {pick('last_login_at')} FROM users").fetchall()
            conn.execute(f"CREATE TABLE users_new ({_USERS_CLASSIC_COLUMNS})")
            conn.executemany(
                "INSERT INTO users_new(user_id, qiita_email, user_level, principal_idx, miint_email, "
                "created_at, updated_at, last_login_at) VALUES(?, NULL, NULL, ?, ?, ?, ?, ?)", rows)
            conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE revoked_at IS NULL", (_now(),))
            conn.execute("DROP TABLE users")
            conn.execute("ALTER TABLE users_new RENAME TO users")
            problems = conn.execute("PRAGMA foreign_key_check(auth_sessions)").fetchall()   # users' only referrer
            if problems:
                raise RuntimeError(f"users migration left {len(problems)} broken foreign keys")
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('users_classic_v1', ?)", (_now(),))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _bootstrap():
    with _conn() as conn:
        _create_schema(conn)
        conn.commit()
    _migrate_users_to_classic()


_bootstrap()
