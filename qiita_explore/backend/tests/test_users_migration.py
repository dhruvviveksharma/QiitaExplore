"""The qiita-miint → classic-Qiita `users` migration (store/db.py
_migrate_users_to_classic), the pre-auth reconcile hook it must not trip, and
the operator bind script (scripts/bind_classic_accounts.py)."""
import importlib.util
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from . import auth_helpers as ah

_CLASSIC = {"user_id", "qiita_email", "user_level", "principal_idx", "miint_email",
            "created_at", "updated_at", "last_login_at"}

_MIINT_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE projects (project_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
                       created_at TEXT, updated_at TEXT);
CREATE TABLE users (
    user_id TEXT PRIMARY KEY, principal_idx INTEGER NOT NULL, email TEXT, system_role TEXT,
    scopes TEXT, profile_complete INTEGER NOT NULL DEFAULT 0,
    created_at TEXT, updated_at TEXT, last_login_at TEXT);
CREATE TABLE auth_sessions (
    session_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, pat_encrypted TEXT NOT NULL, token_idx TEXT,
    source TEXT NOT NULL DEFAULT 'paste', pat_expires_at TEXT, csrf_token TEXT NOT NULL,
    created_at TEXT, last_seen_at TEXT, last_verified_at TEXT, absolute_expires_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE);
"""


def _seed_miint(path):
    future = (datetime.utcnow() + timedelta(hours=12)).isoformat() + "Z"
    conn = sqlite3.connect(path)
    conn.executescript(_MIINT_SCHEMA)
    conn.executemany("INSERT INTO users(user_id, principal_idx, email, created_at, updated_at, last_login_at) "
                     "VALUES(?, ?, ?, 'c', 'u', 'l')", [("11", 11, "one@x.edu"), ("12", 12, "two@x.edu")])
    conn.execute("INSERT INTO auth_sessions(session_hash, user_id, pat_encrypted, csrf_token, absolute_expires_at) "
                 "VALUES('live', '11', '', 'csrf', ?)", (future,))
    conn.execute("INSERT INTO projects VALUES('p1', '11', 'kept', 'c', 'u')")
    conn.commit()
    conn.close()


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


class TestMigration:
    def test_miint_users_rebuilt_in_place(self, tmp_path, monkeypatch):
        db = tmp_path / "miint.db"
        _seed_miint(db)
        _boot(db, monkeypatch)
        assert _cols(db) == _CLASSIC
        users = _q(db, "SELECT * FROM users ORDER BY user_id")
        assert [(u["user_id"], u["principal_idx"], u["miint_email"], u["qiita_email"]) for u in users] == [
            ("11", 11, "one@x.edu", None), ("12", 12, "two@x.edu", None)]
        assert users[0]["created_at"] == "c" and users[0]["last_login_at"] == "l"
        sess = _q(db, "SELECT * FROM auth_sessions")
        assert len(sess) == 1 and sess[0]["revoked_at"]          # kept (no cascade), but revoked
        fk_sql = _q(db, "SELECT sql FROM sqlite_master WHERE name = 'auth_sessions'")[0]["sql"]
        assert "REFERENCES users(user_id)" in fk_sql.replace('"', "")
        assert _q(db, "PRAGMA foreign_key_check") == []
        assert _q(db, "PRAGMA integrity_check")[0]["integrity_check"] == "ok"
        assert _q(db, "SELECT * FROM projects") == [{"project_id": "p1", "user_id": "11", "name": "kept",
                                                     "created_at": "c", "updated_at": "u"}]
        assert _q(db, "SELECT value FROM meta WHERE key = 'users_classic_v1'")
        assert "password_fp" in _cols(db, "auth_sessions")
        assert not _q(db, "SELECT name FROM sqlite_master WHERE name IN ('users_new', 'users_legacy_pre_auth')")

    def test_second_boot_changes_nothing(self, tmp_path, monkeypatch):
        db = tmp_path / "miint.db"
        _seed_miint(db)
        _boot(db, monkeypatch)
        conn = sqlite3.connect(db)
        conn.execute("UPDATE users SET qiita_email = 'one@x.edu' WHERE user_id = '11'")
        conn.commit()
        conn.close()
        before = (_q(db, "SELECT * FROM users"), _q(db, "SELECT * FROM meta"), _q(db, "SELECT * FROM auth_sessions"))
        _boot(db, monkeypatch)
        assert (_q(db, "SELECT * FROM users"), _q(db, "SELECT * FROM meta"),
                _q(db, "SELECT * FROM auth_sessions")) == before

    def test_classic_table_with_zero_sessions_survives_reboot(self, tmp_path, monkeypatch):
        """Regression (review C2): the old reconcile hook took any `users` table
        without principal_idx for the pre-auth one and, with zero sessions,
        renamed it aside — every account's binding would have vanished."""
        db = tmp_path / "fresh.db"
        store_db = _boot(db, monkeypatch)
        with store_db._conn() as conn:
            conn.execute("INSERT INTO users(user_id, qiita_email, user_level) VALUES('qabc', 'a@x.edu', 'user')")
            conn.commit()
        assert _q(db, "SELECT COUNT(1) AS n FROM auth_sessions")[0]["n"] == 0
        _boot(db, monkeypatch)
        _boot(db, monkeypatch)
        assert _q(db, "SELECT user_id, qiita_email FROM users") == [{"user_id": "qabc", "qiita_email": "a@x.edu"}]
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
        assert _cols(db) == _CLASSIC
        assert _q(db, "SELECT username FROM users_legacy_pre_auth") == [{"username": "admin"}]

    def test_crash_before_commit_leaves_the_old_table(self, tmp_path, monkeypatch):
        store_db = _boot(tmp_path / "other.db", monkeypatch)
        db = tmp_path / "miint.db"
        _seed_miint(db)
        monkeypatch.setattr(store_db, "DB_PATH", str(db))
        real_now, calls = store_db._now, {"n": 0}

        def _dies_at_the_marker():           # 1st call: revoking sessions; 2nd: the meta marker
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("power cut")
            return real_now()
        monkeypatch.setattr(store_db, "_now", _dies_at_the_marker)
        with pytest.raises(RuntimeError, match="power cut"):
            store_db._migrate_users_to_classic()
        assert "email" in _cols(db) and "qiita_email" not in _cols(db)
        assert len(_q(db, "SELECT * FROM users")) == 2
        assert _q(db, "SELECT revoked_at FROM auth_sessions") == [{"revoked_at": None}]
        assert not _q(db, "SELECT name FROM sqlite_master WHERE name = 'users_new'")

    def test_two_workers_migrate_once(self, tmp_path, monkeypatch):
        store_db = _boot(tmp_path / "other.db", monkeypatch)
        db = tmp_path / "miint.db"
        _seed_miint(db)
        monkeypatch.setattr(store_db, "DB_PATH", str(db))
        errors = []

        def _run():
            try:
                store_db._migrate_users_to_classic()
            except Exception as exc:     # surfaced below
                errors.append(exc)
        threads = [threading.Thread(target=_run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert _cols(db) == _CLASSIC and len(_q(db, "SELECT * FROM users")) == 2
        assert _q(db, "PRAGMA foreign_key_check") == []


# ── scripts/bind_classic_accounts.py ─────────────────────────────────────────

@pytest.fixture
def bind_script(fresh_db, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts" / "bind_classic_accounts.py"
    spec = importlib.util.spec_from_file_location("bind_classic_accounts", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    table = {a.email: a for a in (ah.account("one@x.edu"),
                                  ah.account("two@x.edu", level="unverified", level_id=5))}
    monkeypatch.setattr(mod.accounts, "fetch_account", lambda email: table.get(email))
    from store.db import _conn
    with _conn() as conn:
        conn.executemany("INSERT INTO users(user_id, principal_idx, miint_email, created_at) VALUES(?, ?, ?, ?)",
                         [("11", 11, "One@x.edu", "1"), ("12", 12, "two@x.edu", "2"),
                          ("13", 13, "three@x.edu", "3")])
        conn.commit()
    return mod


def _bound():
    from store.db import _conn
    with _conn() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT user_id, qiita_email FROM users")}


class TestBindScript:
    def test_dry_run_lists_and_changes_nothing(self, bind_script, capsys):
        assert bind_script.main([]) == 0
        out = capsys.readouterr().out
        assert "11\tprincipal_idx=11\tmiint_email=One@x.edu\tclassic: user" in out
        assert "12\tprincipal_idx=12\tmiint_email=two@x.edu\tclassic: unverified (unverified: can't bind)" in out
        assert "13\tprincipal_idx=13\tmiint_email=three@x.edu\tno classic account" in out
        assert "$2b$" not in out
        assert set(_bound().values()) == {None}

    def test_apply_binds_only_verified_confirmed_pairs(self, bind_script, capsys):
        rc = bind_script.main(["--apply", "11=One@X.edu", "12=two@x.edu", "13=three@x.edu", "garbage"])
        assert rc == 1
        assert _bound() == {"11": "one@x.edu", "12": None, "13": None}
        out = capsys.readouterr().out
        assert "BOUND 11 -> one@x.edu" in out and out.count("SKIP") == 3

    def test_apply_refuses_an_email_already_bound(self, bind_script):
        from store.auth_store import resolve_user
        resolve_user("one@x.edu", "user")              # they logged in before the operator bound
        assert bind_script.main(["--apply", "11=one@x.edu"]) == 1
        assert _bound()["11"] is None

    def test_qiita_unreachable(self, bind_script, monkeypatch):
        def _down(email):
            raise bind_script.accounts.AccountsUnavailable("down")
        monkeypatch.setattr(bind_script.accounts, "fetch_account", _down)
        assert bind_script.main(["--apply", "11=one@x.edu"]) == 2
        assert _bound()["11"] is None

    def test_refuses_to_run_without_the_db_path(self, tmp_path):
        """Unset, store.db would quietly open (and create) backend/data/projects.db."""
        script = Path(__file__).resolve().parents[2] / "scripts" / "bind_classic_accounts.py"
        env = {k: v for k, v in os.environ.items() if k != "QIITA_EXPERIMENT_DB_PATH"}
        r = subprocess.run([sys.executable, str(script)], env=env, cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode == 1 and "QIITA_EXPERIMENT_DB_PATH is not set" in r.stderr
