"""The `users` migration to one account per email (store/db.py
_migrate_users_to_email_v1), from both earlier shapes — qiita-miint
(production) and classic_v1 (branch qiita-classic-login) — and the pre-auth
reconcile hook it must not trip."""
import sqlite3
import sys
import threading
from datetime import datetime, timedelta

import pytest

_CURRENT = {"user_id", "email", "user_level", "principal_idx", "created_at", "updated_at", "last_login_at"}

_COMMON_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE projects (project_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
                       created_at TEXT, updated_at TEXT);
CREATE TABLE auth_sessions (
    session_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, pat_encrypted TEXT NOT NULL, token_idx TEXT,
    source TEXT NOT NULL DEFAULT 'paste', pat_expires_at TEXT, csrf_token TEXT NOT NULL,
    created_at TEXT, last_seen_at TEXT, last_verified_at TEXT, absolute_expires_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE);
"""
_MIINT_USERS = """CREATE TABLE users (
    user_id TEXT PRIMARY KEY, principal_idx INTEGER NOT NULL, email TEXT, system_role TEXT,
    scopes TEXT, profile_complete INTEGER NOT NULL DEFAULT 0,
    created_at TEXT, updated_at TEXT, last_login_at TEXT)"""
_CLASSIC_V1_USERS = """CREATE TABLE users (
    user_id TEXT PRIMARY KEY, qiita_email TEXT UNIQUE, user_level TEXT, principal_idx INTEGER,
    miint_email TEXT, created_at TEXT, updated_at TEXT, last_login_at TEXT)"""


def _future():
    return (datetime.utcnow() + timedelta(hours=12)).isoformat() + "Z"


def _seed(path, users_ddl, users, sessions=("11",)):
    conn = sqlite3.connect(path)
    conn.executescript(_COMMON_SCHEMA)
    conn.execute(users_ddl)
    for row in users:
        cols = ", ".join(row)
        conn.execute(f"INSERT INTO users({cols}) VALUES({', '.join('?' * len(row))})", tuple(row.values()))
    for i, uid in enumerate(sessions):
        conn.execute("INSERT INTO auth_sessions(session_hash, user_id, pat_encrypted, csrf_token, "
                     "absolute_expires_at) VALUES(?, ?, '', 'csrf', ?)", (f"s{i}", uid, _future()))
    conn.execute("INSERT INTO projects VALUES('p1', '11', 'kept', 'c', 'u')")
    conn.commit()
    conn.close()


def _seed_miint(path):
    _seed(path, _MIINT_USERS, [
        {"user_id": "11", "principal_idx": 11, "email": "One@X.edu", "created_at": "c", "last_login_at": "l"},
        {"user_id": "12", "principal_idx": 12, "email": "two@x.edu", "created_at": "c", "last_login_at": "l"},
    ])


def _boot(path, monkeypatch):
    """Import store.db (which runs _bootstrap) against `path`."""
    monkeypatch.setenv("QIITA_EXPERIMENT_DB_PATH", str(path))
    for name in list(sys.modules):
        if name == "store" or name.startswith("store."):
            del sys.modules[name]
    import store.db
    return store.db


def _q(path, sql, *args):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _cols(path, table="users"):
    return {r["name"] for r in _q(path, f"PRAGMA table_info({table})")}


def _users(path):
    return [(u["user_id"], u["email"], u["principal_idx"], u["user_level"])
            for u in _q(path, "SELECT * FROM users ORDER BY user_id")]


class TestFromQiitaMiint:
    def test_rows_get_their_email_and_keep_everything(self, tmp_path, monkeypatch):
        db = tmp_path / "miint.db"
        _seed_miint(db)
        _boot(db, monkeypatch)
        assert _cols(db) == _CURRENT
        assert _users(db) == [("11", "one@x.edu", 11, None), ("12", "two@x.edu", 12, None)]
        assert _q(db, "SELECT created_at, last_login_at FROM users WHERE user_id='11'") == [
            {"created_at": "c", "last_login_at": "l"}]
        assert _q(db, "SELECT revoked_at FROM auth_sessions") == [{"revoked_at": None}]    # still signed in
        fk_sql = _q(db, "SELECT sql FROM sqlite_master WHERE name = 'auth_sessions'")[0]["sql"]
        assert "REFERENCES users(user_id)" in fk_sql.replace('"', "")
        assert _q(db, "PRAGMA foreign_key_check") == []
        assert _q(db, "PRAGMA integrity_check")[0]["integrity_check"] == "ok"
        assert _q(db, "SELECT name FROM projects") == [{"name": "kept"}]
        assert _q(db, "SELECT value FROM meta WHERE key = 'users_email_v1'")
        assert _q(db, "SELECT name FROM sqlite_master WHERE name = 'idx_users_principal'")
        assert not _q(db, "SELECT name FROM sqlite_master WHERE name IN ('users_new', 'users_legacy_pre_auth')")

    def test_then_both_logins_reach_the_old_account(self, tmp_path, monkeypatch):
        db = tmp_path / "miint.db"
        _seed_miint(db)
        _boot(db, monkeypatch)
        from store.auth_store import resolve_user
        assert resolve_user("one@x.edu", level="user") == "11"            # classic
        assert resolve_user("two@x.edu", principal_idx=12) == "12"         # qiita-miint

    def test_shared_email_kept_by_latest_login(self, tmp_path, monkeypatch, caplog):
        db = tmp_path / "dup.db"
        _seed(db, _MIINT_USERS, [
            {"user_id": "21", "principal_idx": 21, "email": "same@x.edu", "last_login_at": "2026-01-01"},
            {"user_id": "22", "principal_idx": 22, "email": "SAME@x.edu", "last_login_at": "2026-09-01"},
            {"user_id": "23", "principal_idx": 23, "email": "kelvinK@x.edu", "last_login_at": "2026-09-02"},
        ], sessions=())
        _boot(db, monkeypatch)
        assert _users(db) == [("21", None, 21, None), ("22", "same@x.edu", 22, None), ("23", None, 23, None)]
        assert "21" in caplog.text and "23" in caplog.text           # shared, and unusable


class TestFromClassicV1:
    def test_qiita_email_first_then_miint_email(self, tmp_path, monkeypatch):
        db = tmp_path / "classic.db"
        _seed(db, _CLASSIC_V1_USERS, [
            # an old qiita-miint account, and the classic account its owner made since
            {"user_id": "11", "principal_idx": 11, "miint_email": "me@x.edu", "last_login_at": "2026-10-13"},
            {"user_id": "qnew", "qiita_email": "me@x.edu", "user_level": "user", "last_login_at": "2026-10-12"},
            {"user_id": "12", "principal_idx": 12, "miint_email": "other@x.edu"},
        ])
        _boot(db, monkeypatch)
        assert _users(db) == [("11", None, 11, None), ("12", "other@x.edu", 12, None),
                              ("qnew", "me@x.edu", None, "user")]
        assert _q(db, "SELECT revoked_at FROM auth_sessions") == [{"revoked_at": None}]


class TestMigrationSafety:
    def test_second_boot_changes_nothing(self, tmp_path, monkeypatch):
        db = tmp_path / "miint.db"
        _seed_miint(db)
        _boot(db, monkeypatch)
        before = (_q(db, "SELECT * FROM users"), _q(db, "SELECT * FROM meta"), _q(db, "SELECT * FROM auth_sessions"))
        _boot(db, monkeypatch)
        assert (_q(db, "SELECT * FROM users"), _q(db, "SELECT * FROM meta"),
                _q(db, "SELECT * FROM auth_sessions")) == before

    def test_fresh_db_with_zero_sessions_survives_reboots(self, tmp_path, monkeypatch):
        """Regression (review C2): a later users shape must never be mistaken
        for the pre-auth one and renamed aside."""
        db = tmp_path / "fresh.db"
        store_db = _boot(db, monkeypatch)
        with store_db._conn() as conn:
            conn.execute("INSERT INTO users(user_id, email, user_level) VALUES('qabc', 'a@x.edu', 'user')")
            conn.commit()
        _boot(db, monkeypatch)
        _boot(db, monkeypatch)
        assert _q(db, "SELECT user_id, email FROM users") == [{"user_id": "qabc", "email": "a@x.edu"}]
        assert not _q(db, "SELECT name FROM sqlite_master WHERE name = 'users_legacy_pre_auth'")

    def test_pre_auth_table_still_moved_aside(self, tmp_path, monkeypatch):
        db = tmp_path / "preauth.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, email TEXT, "
                     "password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', created_at TEXT)")
        conn.execute("INSERT INTO users VALUES('uuid-x', 'admin', NULL, 'hash', 'admin', 't')")
        conn.commit()
        conn.close()
        _boot(db, monkeypatch)
        assert _cols(db) == _CURRENT
        assert _q(db, "SELECT username FROM users_legacy_pre_auth") == [{"username": "admin"}]

    def test_crash_before_commit_leaves_the_old_table(self, tmp_path, monkeypatch):
        store_db = _boot(tmp_path / "other.db", monkeypatch)
        db = tmp_path / "miint.db"
        _seed_miint(db)
        monkeypatch.setattr(store_db, "DB_PATH", str(db))

        def _dies_at_the_marker():
            raise RuntimeError("power cut")
        monkeypatch.setattr(store_db, "_now", _dies_at_the_marker)
        with pytest.raises(RuntimeError, match="power cut"):
            store_db._migrate_users_to_email_v1()
        assert "system_role" in _cols(db) and "user_level" not in _cols(db)
        assert len(_q(db, "SELECT * FROM users")) == 2
        assert not _q(db, "SELECT name FROM sqlite_master WHERE name IN ('users_new', 'idx_users_principal')")

    def test_two_workers_migrate_once(self, tmp_path, monkeypatch):
        store_db = _boot(tmp_path / "other.db", monkeypatch)
        db = tmp_path / "miint.db"
        _seed_miint(db)
        monkeypatch.setattr(store_db, "DB_PATH", str(db))
        errors = []

        def _run():
            try:
                store_db._migrate_users_to_email_v1()
            except Exception as exc:     # surfaced below
                errors.append(exc)
        threads = [threading.Thread(target=_run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert _cols(db) == _CURRENT and len(_q(db, "SELECT * FROM users")) == 2
        assert _q(db, "PRAGMA foreign_key_check") == []
