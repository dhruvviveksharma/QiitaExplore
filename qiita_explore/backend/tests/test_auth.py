"""Tests for the paste-PAT Qiita authentication system: sessions,
legacy-claim, and full Flask route behavior with a mocked Qiita whoami (no
network dependency — see test_auth_smoke.py for the real-control-plane check).
"""
import os
import sys
import time
from datetime import datetime, timedelta

import pytest

from .conftest import stub_qiita_db_and_core

# ── module-scoped Flask app wired to its own isolated SQLite file ───────────


@pytest.fixture(scope="module")
def auth_db_path(tmp_path_factory):
    return str(tmp_path_factory.mktemp("auth_test") / "test.db")


@pytest.fixture(scope="module")
def _app(auth_db_path):
    """Import run.app once per test module — expensive (imports every route
    module) and safe to share, since the Flask app itself holds no per-test
    state. Per-test isolation comes from api_client's fresh cookie jar below."""
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = auth_db_path

    # Purge any previously-imported instance so this module gets a clean,
    # deterministic bind to db_path regardless of test execution order.
    for name in list(sys.modules):
        if name == "run" or name.startswith("routes.") or name == "store" or name.startswith("store.") or "sql_store" in name:
            del sys.modules[name]

    stub_qiita_db_and_core()

    import run
    import config
    config.ALLOWED_ORIGINS = []
    config.SESSION_COOKIE_SECURE = False
    config.QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX = None
    return run.app


@pytest.fixture
def api_client(_app):
    """Fresh test client (empty cookie jar) per test — the shared _app import
    must never leak a session cookie from one test into the next."""
    return _app.test_client()


@pytest.fixture
def mock_whoami(monkeypatch):
    """Patch routes.auth_routes.whoami to a controllable fake, keyed by token
    string -> WhoAmIResult, with no network call."""
    import routes.auth_routes as auth_routes
    from helpers.qiita_client import WhoAmIResult

    responses = {}
    call_count = {"n": 0}

    def _fake_whoami(pat):
        call_count["n"] += 1
        if pat in responses:
            return responses[pat]
        return WhoAmIResult(ok=False, transient_error=False)

    monkeypatch.setattr(auth_routes, "whoami", _fake_whoami)

    def register(pat, result):
        responses[pat] = result

    register.WhoAmIResult = WhoAmIResult
    register.call_count = call_count
    return register


def _human(principal_idx, email="user@test.local", scopes=None, profile_complete=True):
    from helpers.qiita_client import WhoAmIResult
    return WhoAmIResult(ok=True, identity={
        "kind": "human",
        "principal_idx": principal_idx,
        "email": email,
        "system_role": "user",
        "scopes": scopes or [],
        "profile_complete": profile_complete,
    })


def _connect(api_client, mock_whoami, token, principal_idx, **kwargs):
    """Connect a fresh session for a synthetic principal; returns (headers, user_id)."""
    mock_whoami(token, _human(principal_idx, **kwargs))
    resp = api_client.post("/api/auth/connect", json={"token": token})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    return {"X-CSRF-Token": body["csrf_token"]}, body["user_id"]


# ── Unit: qiita_client.whoami (mocks httpx, no network) ──────────────────────

class _FakeHttpxResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class TestQiitaClientWhoami:
    def test_human_kind_accepted(self, monkeypatch):
        import httpx
        from helpers import qiita_client
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeHttpxResponse(200, {"kind": "human", "principal_idx": 1}))
        result = qiita_client.whoami("qk_x")
        assert result.ok is True
        assert result.identity["principal_idx"] == 1

    def test_service_kind_rejected(self, monkeypatch):
        import httpx
        from helpers import qiita_client
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeHttpxResponse(200, {"kind": "service", "principal_idx": 7}))
        result = qiita_client.whoami("qk_x")
        assert result.ok is False
        assert result.transient_error is False

    def test_anonymous_kind_rejected(self, monkeypatch):
        import httpx
        from helpers import qiita_client
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeHttpxResponse(200, {"kind": "anonymous"}))
        result = qiita_client.whoami("qk_x")
        assert result.ok is False
        assert result.transient_error is False

    def test_401_is_not_transient(self, monkeypatch):
        import httpx
        from helpers import qiita_client
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeHttpxResponse(401, {}))
        result = qiita_client.whoami("qk_x")
        assert result.ok is False
        assert result.transient_error is False

    @pytest.mark.parametrize("status", [403, 404, 302, 400, 429])
    def test_ambiguous_status_is_transient(self, monkeypatch, status):
        """Only a 401 (or a 200 with a non-human kind) definitively says the PAT
        is bad. Treating every other status as definitive is what silently
        destroyed live sessions — a 403 or a proxy redirect says nothing about
        the credential, so callers must not revoke on it."""
        import httpx
        from helpers import qiita_client
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeHttpxResponse(status, {}))
        result = qiita_client.whoami("qk_x")
        assert result.ok is False
        assert result.transient_error is True, f"{status} must not be treated as a definitive rejection"

    def test_5xx_is_transient(self, monkeypatch):
        import httpx
        from helpers import qiita_client
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeHttpxResponse(503, {}))
        result = qiita_client.whoami("qk_x")
        assert result.ok is False
        assert result.transient_error is True

    def test_connection_error_is_transient(self, monkeypatch):
        import httpx
        from helpers import qiita_client

        def _raise(*a, **k):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "get", _raise)
        result = qiita_client.whoami("qk_x")
        assert result.ok is False
        assert result.transient_error is True


# ── Unit: auth_store sessions ────────────────────────────────────────────────

class TestAuthStore:
    def test_session_create_and_lookup(self, fresh_db):
        from store.auth_store import upsert_user, create_session, get_session_by_token
        uid = upsert_user(principal_idx=1, email="a@b", system_role="user", scopes=[], profile_complete=True)
        raw, csrf = create_session(user_id=uid, source="paste")
        sess = get_session_by_token(raw)
        assert sess is not None
        assert sess["user_id"] == uid
        assert sess["csrf_token"] == csrf
        assert sess["pat_encrypted"] == ""

    def test_unknown_token_returns_none(self, fresh_db):
        from store.auth_store import get_session_by_token
        assert get_session_by_token("nonexistent") is None

    def test_revoked_session_rejected(self, fresh_db):
        from store.auth_store import upsert_user, create_session, get_session_by_token, revoke_session
        uid = upsert_user(principal_idx=2, email="a@b", system_role="user", scopes=[], profile_complete=True)
        raw, _ = create_session(user_id=uid, source="paste")
        sess = get_session_by_token(raw)
        revoke_session(sess["session_hash"])
        assert get_session_by_token(raw) is None

    def test_absolute_expiry_enforced(self, fresh_db, monkeypatch):
        import config
        from store.auth_store import upsert_user, create_session, get_session_by_token
        monkeypatch.setattr(config, "AUTH_SESSION_ABSOLUTE_TTL_SECONDS", 1)
        uid = upsert_user(principal_idx=3, email="a@b", system_role="user", scopes=[], profile_complete=True)
        raw, _ = create_session(user_id=uid, source="paste")
        assert get_session_by_token(raw) is not None
        time.sleep(1.2)
        assert get_session_by_token(raw) is None

    def test_idle_session_still_resolves(self, fresh_db):
        """Sitting unused is not a reason to sign someone out."""
        from store.auth_store import upsert_user, create_session, get_session_by_token
        from store.db import _conn
        uid = upsert_user(principal_idx=4, email="a@b", system_role="user", scopes=[], profile_complete=True)
        raw, _ = create_session(user_id=uid, source="paste")
        sess = get_session_by_token(raw)
        stale = (datetime.utcnow() - timedelta(hours=12)).isoformat() + "Z"
        with _conn() as conn:
            conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE session_hash = ?",
                         (stale, sess["session_hash"]))
            conn.commit()
        assert get_session_by_token(raw) is not None

    def test_touch_is_throttled(self, fresh_db):
        """last_seen_at is informational now, so it must not cost a write per
        request — that was 8 concurrent writers against one WAL file."""
        from store.auth_store import upsert_user, create_session, get_session_by_token, touch_session
        uid = upsert_user(principal_idx=9, email="a@b", system_role="user", scopes=[], profile_complete=True)
        raw, _ = create_session(user_id=uid, source="paste")
        sess = get_session_by_token(raw)

        # Fresh last_seen_at → no write.
        touch_session(sess["session_hash"], sess["last_seen_at"])
        assert get_session_by_token(raw)["last_seen_at"] == sess["last_seen_at"]

        # Stale last_seen_at → write.
        stale = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
        touch_session(sess["session_hash"], stale)
        assert get_session_by_token(raw)["last_seen_at"] != sess["last_seen_at"]

    def test_touch_does_not_extend_absolute_expiry(self, fresh_db, monkeypatch):
        import config
        from store.auth_store import upsert_user, create_session, get_session_by_token, touch_session
        monkeypatch.setattr(config, "AUTH_SESSION_ABSOLUTE_TTL_SECONDS", 1)
        uid = upsert_user(principal_idx=5, email="a@b", system_role="user", scopes=[], profile_complete=True)
        raw, _ = create_session(user_id=uid, source="paste")
        sess = get_session_by_token(raw)
        touch_session(sess["session_hash"])
        time.sleep(1.2)
        assert get_session_by_token(raw) is None  # touch must not push past absolute expiry

    def test_replace_session_revokes_prior(self, fresh_db):
        from store.auth_store import upsert_user, create_session, get_session_by_token
        uid = upsert_user(principal_idx=6, email="a@b", system_role="user", scopes=[], profile_complete=True)
        raw_a, _ = create_session(user_id=uid, source="paste")
        sess_a = get_session_by_token(raw_a)
        raw_b, _ = create_session(user_id=uid, source="paste", replace_session_hash=sess_a["session_hash"])
        assert get_session_by_token(raw_a) is None
        assert get_session_by_token(raw_b) is not None


# ── Unit: legacy_claim ───────────────────────────────────────────────────────

class TestLegacyClaim:
    def test_disabled_when_unset(self, fresh_db, monkeypatch):
        import config
        from store.legacy_claim import claim_eligible
        monkeypatch.setattr(config, "QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX", None)
        assert claim_eligible("42") is False

    def test_disabled_for_non_claimant(self, fresh_db, monkeypatch):
        import config
        from store.legacy_claim import claim_eligible
        monkeypatch.setattr(config, "QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX", 42)
        assert claim_eligible("99") is False

    def test_claim_reassigns_and_marks_meta(self, fresh_db, monkeypatch, crud):
        import config
        from store.legacy_claim import claim_eligible, claim_legacy_default, legacy_default_counts
        monkeypatch.setattr(config, "QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX", 42)
        crud.create_project("default", "Legacy Proj")
        assert claim_eligible("42") is True
        counts = legacy_default_counts()
        assert counts["projects"] == 1
        result = claim_legacy_default("42")
        assert result["projects"] == 1
        assert claim_eligible("42") is False  # already claimed

    def test_second_claim_conflicts(self, fresh_db, monkeypatch, crud):
        import config
        from store.legacy_claim import claim_legacy_default, LegacyClaimConflict
        monkeypatch.setattr(config, "QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX", 42)
        crud.create_project("default", "Legacy Proj")
        claim_legacy_default("42")
        with pytest.raises(ValueError):
            claim_legacy_default("42")  # claim_eligible() now False -> ValueError, not the DB race path


# ── Single-login behavior ────────────────────────────────────────────────────

class TestSingleLogin:
    def test_whoami_called_only_at_connect(self, api_client, mock_whoami):
        headers, _ = _connect(api_client, mock_whoami, "qk_once", 401)
        assert mock_whoami.call_count["n"] == 1
        api_client.get("/api/auth/me")
        api_client.get("/api/projects", headers=headers)
        assert mock_whoami.call_count["n"] == 1

    def test_connect_does_not_persist_pat(self, api_client, mock_whoami, auth_db_path):
        import sqlite3
        _connect(api_client, mock_whoami, "qk_nostore", 402)
        conn = sqlite3.connect(auth_db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT pat_encrypted FROM auth_sessions").fetchone()
        conn.close()
        assert row["pat_encrypted"] == ""

    def test_legacy_pat_ciphertext_scrubbed_on_bootstrap(self, tmp_path, monkeypatch):
        import sqlite3

        db_path = str(tmp_path / "legacy_pat.db")
        seed_conn = sqlite3.connect(db_path)
        seed_conn.execute(
            """
            CREATE TABLE auth_sessions (
                session_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                pat_encrypted TEXT NOT NULL,
                token_idx TEXT,
                source TEXT NOT NULL DEFAULT 'paste',
                pat_expires_at TEXT,
                csrf_token TEXT NOT NULL,
                created_at TEXT,
                last_seen_at TEXT,
                last_verified_at TEXT,
                absolute_expires_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """
        )
        future = (datetime.utcnow() + timedelta(hours=12)).isoformat() + "Z"
        seed_conn.execute(
            """
            INSERT INTO auth_sessions(
                session_hash, user_id, pat_encrypted, csrf_token,
                created_at, last_seen_at, absolute_expires_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            ("hash1", "1", "gAAAAAlegacy", "csrf", "t", "t", future),
        )
        seed_conn.commit()
        seed_conn.close()

        monkeypatch.setenv("QIITA_EXPERIMENT_DB_PATH", db_path)
        for mod_name in list(sys.modules.keys()):
            if "sql_store" in mod_name or "store" in mod_name:
                del sys.modules[mod_name]

        import store.db as sql_store_db

        with sql_store_db._conn() as conn:
            row = conn.execute(
                "SELECT pat_encrypted FROM auth_sessions WHERE session_hash = ?", ("hash1",)
            ).fetchone()
            assert row["pat_encrypted"] == ""

    def test_relogin_replaces_prior_session(self, api_client, mock_whoami, _app):
        _connect(api_client, mock_whoami, "qk_relogin_a", 501)
        old_cookie = api_client.get_cookie("qe_sid").value
        stale_client = _app.test_client()
        stale_client.set_cookie("qe_sid", old_cookie)
        assert stale_client.get("/api/auth/me").get_json()["user_id"] == "501"

        mock_whoami("qk_relogin_b", _human(502))
        resp = api_client.post("/api/auth/connect", json={"token": "qk_relogin_b"})
        assert resp.status_code == 200
        assert stale_client.get("/api/auth/me").get_json() == {"anonymous": True}
        assert api_client.get("/api/auth/me").get_json()["user_id"] == "502"

    def test_failed_relogin_preserves_current_session(self, api_client, mock_whoami):
        from helpers.qiita_client import WhoAmIResult
        _, uid = _connect(api_client, mock_whoami, "qk_keep", 503)

        mock_whoami("qk_bad_relogin", WhoAmIResult(ok=False, transient_error=False))
        resp = api_client.post("/api/auth/connect", json={"token": "qk_bad_relogin"})
        assert resp.status_code == 401
        me = api_client.get("/api/auth/me")
        assert me.get_json()["user_id"] == uid


# ── Route: connect / me / logout ─────────────────────────────────────────────

class TestAuthRoutes:
    def test_login_url(self, api_client):
        resp = api_client.get("/api/auth/login-url")
        assert resp.status_code == 200
        assert resp.get_json()["url"].endswith("/api/v1/auth/login")

    def test_login_url_wraps_in_logout_when_loginrocket_set(self, api_client, monkeypatch):
        # With QIITA_LOGINROCKET_URL set, the entry routes through LoginRocket
        # /logout first (clears a cached AuthRocket session), with the control-
        # plane login carried as the (single-encoded) redirect_uri.
        import config
        from urllib.parse import parse_qs, urlparse

        monkeypatch.setattr(config, "QIITA_LOGINROCKET_URL", "https://realm.e2.loginrocket.com")
        resp = api_client.get("/api/auth/login-url")
        assert resp.status_code == 200
        url = resp.get_json()["url"]
        assert url.startswith("https://realm.e2.loginrocket.com/logout?redirect_uri=")
        assert url.count("?") == 1  # the inner login URL's query is encoded away
        inner = parse_qs(urlparse(url).query)["redirect_uri"][0]
        assert inner.endswith("/api/v1/auth/login")

    def test_me_anonymous(self, api_client):
        resp = api_client.get("/api/auth/me")
        assert resp.get_json() == {"anonymous": True}

    def test_connect_missing_token(self, api_client):
        resp = api_client.post("/api/auth/connect", json={})
        assert resp.status_code == 400

    def test_connect_invalid_token(self, api_client, mock_whoami):
        resp = api_client.post("/api/auth/connect", json={"token": "qk_bad"})
        assert resp.status_code == 401

    def test_connect_rejects_service_principal(self, api_client, mock_whoami):
        # qiita_client.whoami() itself is the enforcement point for non-human
        # kinds (see TestQiitaClientWhoami.test_service_kind_rejected below) —
        # here we just confirm the route correctly surfaces ok=False as 401.
        from helpers.qiita_client import WhoAmIResult
        mock_whoami("qk_service", WhoAmIResult(ok=False, transient_error=False))
        resp = api_client.post("/api/auth/connect", json={"token": "qk_service"})
        assert resp.status_code == 401

    def test_connect_transient_upstream_failure(self, api_client, mock_whoami):
        from helpers.qiita_client import WhoAmIResult
        mock_whoami("qk_flaky", WhoAmIResult(ok=False, transient_error=True))
        resp = api_client.post("/api/auth/connect", json={"token": "qk_flaky"})
        assert resp.status_code == 503

    def test_connect_success_sets_cookie_and_me_persists(self, api_client, mock_whoami):
        headers, user_id = _connect(api_client, mock_whoami, "qk_good_1", 101)
        me = api_client.get("/api/auth/me")
        assert me.get_json()["user_id"] == user_id
        assert me.get_json()["csrf_token"] == headers["X-CSRF-Token"]

    def test_protected_route_requires_auth(self, api_client):
        resp = api_client.get("/api/projects")
        assert resp.status_code == 401

    def test_public_route_still_works_unauthenticated(self, api_client):
        # /api/auth/* bootstrap endpoints are the only public ones; confirm
        # the guard doesn't accidentally block them.
        resp = api_client.get("/api/auth/login-url")
        assert resp.status_code == 200

    def test_csrf_required_for_state_changing_requests(self, api_client, mock_whoami):
        headers, _ = _connect(api_client, mock_whoami, "qk_good_2", 102)
        no_csrf = api_client.post("/api/projects", json={"name": "x"})
        assert no_csrf.status_code == 403
        wrong_csrf = api_client.post("/api/projects", json={"name": "x"}, headers={"X-CSRF-Token": "nope"})
        assert wrong_csrf.status_code == 403
        ok = api_client.post("/api/projects", json={"name": "x"}, headers=headers)
        assert ok.status_code == 200

    def test_logout_clears_session(self, api_client, mock_whoami):
        headers, _ = _connect(api_client, mock_whoami, "qk_good_3", 103)
        resp = api_client.post("/api/auth/logout", headers=headers)
        assert resp.status_code == 200
        assert api_client.get("/api/auth/me").get_json() == {"anonymous": True}
        assert api_client.get("/api/projects").status_code == 401


# ── Cross-user isolation (the core security goal) ────────────────────────────

class TestCrossUserIsolation:
    def test_projects_isolated(self, api_client, mock_whoami):
        h_a, uid_a = _connect(api_client, mock_whoami, "qk_iso_a1", 201)
        resp = api_client.post("/api/projects", json={"name": "A's project"}, headers=h_a)
        proj = resp.get_json()
        assert proj["user_id"] == uid_a

        h_b, _ = _connect(api_client, mock_whoami, "qk_iso_b1", 202)
        assert api_client.get(f"/api/projects/{proj['project_id']}", headers=h_b).status_code == 404
        assert api_client.get("/api/projects", headers=h_b).get_json()["projects"] == []
        # B cannot delete A's project either
        api_client.delete(f"/api/projects/{proj['project_id']}", headers=h_b)
        h_a2, _ = _connect(api_client, mock_whoami, "qk_iso_a1", 201)
        assert api_client.get(f"/api/projects/{proj['project_id']}", headers=h_a2).status_code == 200

    def test_global_chats_isolated(self, api_client, mock_whoami):
        h_a, _ = _connect(api_client, mock_whoami, "qk_iso_a2", 203)
        chat = api_client.post("/api/global-chats", json={"title": "A chat"}, headers=h_a).get_json()

        h_b, _ = _connect(api_client, mock_whoami, "qk_iso_b2", 204)
        assert api_client.get(f"/api/global-chats/{chat['chat_id']}", headers=h_b).status_code == 404

    def test_merge_workspace_study_mutations_owner_scoped(self, api_client, mock_whoami):
        h_a, _ = _connect(api_client, mock_whoami, "qk_iso_a3", 205)
        ws = api_client.post("/api/merge-workspaces", json={"name": "WS A"}, headers=h_a).get_json()

        h_b, _ = _connect(api_client, mock_whoami, "qk_iso_b3", 206)
        add = api_client.post(f"/api/merge-workspaces/{ws['workspace_id']}/studies",
                               json={"study_id": 111}, headers=h_b)
        assert add.status_code == 404
        rm = api_client.delete(f"/api/merge-workspaces/{ws['workspace_id']}/studies/111", headers=h_b)
        assert rm.status_code == 404
        upd = api_client.patch(f"/api/merge-workspaces/{ws['workspace_id']}/studies/111",
                                json={"chosen_artifact_ids": [1]}, headers=h_b)
        assert upd.status_code == 404
        jobs = api_client.get(f"/api/merge-workspaces/{ws['workspace_id']}/jobs", headers=h_b)
        assert jobs.get_json() == []  # not an error, just correctly empty for B

    def test_user_id_spoofing_ignored(self, api_client, mock_whoami):
        """A client-supplied user_id must never override the session identity."""
        h_a, uid_a = _connect(api_client, mock_whoami, "qk_iso_a4", 207)
        h_b, uid_b = _connect(api_client, mock_whoami, "qk_iso_b4", 208)
        # B tries to create a project claiming to be A via a spoofed body field.
        resp = api_client.post(f"/api/projects?user_id={uid_a}", json={"name": "spoof", "user_id": uid_a}, headers=h_b)
        assert resp.get_json()["user_id"] == uid_b  # server derives identity from session only


# ── Legacy pre-auth `users` table migration ──────────────────────────────────

class TestLegacyUsersMigration:
    def test_legacy_users_table_is_migrated_on_bootstrap(self, tmp_path, monkeypatch):
        """A deployed DB can carry a stale pre-auth `users` table (see
        db.py:_reconcile_legacy_users_table). Bootstrapping against it must
        rename the old table aside and create the current-shape `users`
        table, without losing the legacy rows or breaking upsert_user."""
        import sqlite3

        db_path = str(tmp_path / "legacy.db")
        seed_conn = sqlite3.connect(db_path)
        seed_conn.execute(
            """
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT
            )
            """
        )
        seed_conn.execute(
            "INSERT INTO users(user_id, username, email, password_hash, role, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            ("uuid-x", "admin", None, "hash", "admin", "t"),
        )
        seed_conn.commit()
        seed_conn.close()

        monkeypatch.setenv("QIITA_EXPERIMENT_DB_PATH", db_path)
        for mod_name in list(sys.modules.keys()):
            if "sql_store" in mod_name or "store" in mod_name:
                del sys.modules[mod_name]

        import store.db as sql_store_db  # noqa: F401 — import runs _bootstrap()

        with sql_store_db._conn() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            assert "principal_idx" in cols

            legacy_row = conn.execute(
                "SELECT username FROM users_legacy_pre_auth"
            ).fetchone()
            assert legacy_row["username"] == "admin"

        from store.auth_store import upsert_user
        user_id = upsert_user(
            principal_idx=5, email="a@b", system_role="user", scopes=[], profile_complete=True
        )
        assert user_id == "5"

    def test_fresh_db_bootstrap_is_a_noop_migration(self, fresh_db):
        """On a brand-new DB there's no legacy table to reconcile — `users`
        must be created directly with `principal_idx`, and no
        `users_legacy_pre_auth` aside table should ever appear."""
        import store.db as sql_store_db

        with sql_store_db._conn() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            assert "principal_idx" in cols

            legacy_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users_legacy_pre_auth'"
            ).fetchone()
            assert legacy_table is None
