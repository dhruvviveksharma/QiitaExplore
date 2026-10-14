"""Classic-Qiita email + password login: the session store, POST /api/auth/login
and its checks in order, the per-request re-check against Qiita, cross-user
isolation, and the TKT-042 settings lock. Classic Qiita's account table is
faked (tests/auth_helpers.py); bcrypt is real. Limiter and account-helper unit
tests: test_login_limiter.py. Migration and bind script: test_users_migration.py.
"""
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import pytest

from . import auth_helpers as ah
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
    # helpers.* must go too: auth_middleware imports store.auth_store at
    # import time, so a surviving copy would keep reading sessions from the
    # previous module's SQLite file and every /api/auth/me here would 401.
    for name in list(sys.modules):
        if (name == "run" or name.startswith("routes.") or name == "store" or name.startswith("store.")
                or name.startswith("helpers.") or "sql_store" in name):
            del sys.modules[name]
    stub_qiita_db_and_core()
    import run
    import config
    ah.configure(config)
    return run.app


@pytest.fixture
def api_client(_app):
    """Fresh test client (empty cookie jar) per test — the shared _app import
    must never leak a session cookie from one test into the next."""
    return _app.test_client()


@pytest.fixture
def accts(monkeypatch, _app):
    """classic Qiita's table: alice (user), bob (user), una (unverified)."""
    return ah.fake_accounts(
        monkeypatch,
        ah.account("alice@test.edu"), ah.account("bob@test.edu"),
        ah.account("una@test.edu", level="unverified", level_id=5))


def _sessions(db_path, where="1=1"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM auth_sessions WHERE {where}")]
    finally:
        conn.close()


def _age_verification(db_path, seconds):
    then = (datetime.utcnow() - timedelta(seconds=seconds)).isoformat() + "Z"
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE auth_sessions SET last_verified_at = ? WHERE revoked_at IS NULL", (then,))
    conn.commit()
    conn.close()


# ── Unit: store/auth_store ──────────────────────────────────────────────────

class TestAuthStore:
    def _session(self, **kw):
        from store.auth_store import create_session, resolve_user
        uid = resolve_user("a@b.edu", level="user")
        return uid, create_session(user_id=uid, source="qiita-classic", password_fp="fp", **kw)

    def test_session_create_and_lookup(self, fresh_db):
        from store.auth_store import get_session_by_token
        uid, (raw, csrf) = self._session()
        sess = get_session_by_token(raw)
        assert sess["user_id"] == uid and sess["csrf_token"] == csrf
        assert sess["email"] == "a@b.edu" and sess["password_fp"] == "fp"
        assert sess["last_verified_at"] and sess["pat_encrypted"] == ""

    def test_unknown_token_returns_none(self, fresh_db):
        from store.auth_store import get_session_by_token
        assert get_session_by_token("nonexistent") is None

    def test_revoked_session_rejected(self, fresh_db):
        from store.auth_store import get_session_by_token, revoke_session
        _, (raw, _) = self._session()
        revoke_session(get_session_by_token(raw)["session_hash"])
        assert get_session_by_token(raw) is None

    def test_absolute_expiry_enforced_and_not_extended_by_touch(self, fresh_db, monkeypatch):
        import config
        from store.auth_store import get_session_by_token, touch_session
        monkeypatch.setattr(config, "AUTH_SESSION_ABSOLUTE_TTL_SECONDS", 1)
        _, (raw, _) = self._session()
        touch_session(get_session_by_token(raw)["session_hash"])
        time.sleep(1.2)
        assert get_session_by_token(raw) is None

    def test_touch_is_throttled(self, fresh_db):
        from store.auth_store import get_session_by_token, touch_session
        _, (raw, _) = self._session()
        sess = get_session_by_token(raw)
        touch_session(sess["session_hash"], sess["last_seen_at"])
        assert get_session_by_token(raw)["last_seen_at"] == sess["last_seen_at"]
        touch_session(sess["session_hash"], (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z")
        assert get_session_by_token(raw)["last_seen_at"] != sess["last_seen_at"]

    def test_replace_session_revokes_prior(self, fresh_db):
        from store.auth_store import get_session_by_token
        _, (raw_a, _) = self._session()
        _, (raw_b, _) = self._session(replace_session_hash=get_session_by_token(raw_a)["session_hash"])
        assert get_session_by_token(raw_a) is None and get_session_by_token(raw_b) is not None

    def test_resolve_user_is_stable_and_opaque(self, fresh_db):
        from store.auth_store import get_user, resolve_user
        uid = resolve_user("x@y.edu", level="user")
        assert uid.startswith("q") and len(uid) == 17
        assert resolve_user("x@y.edu", level="admin") == uid
        assert get_user(uid)["user_level"] == "admin"
        assert resolve_user("x@y.edu", principal_idx=7) == uid               # qiita-miint, same email
        assert get_user(uid)["user_level"] == "admin" and get_user(uid)["principal_idx"] == 7
        assert resolve_user("other@y.edu", level="user") != uid             # different email, different account

    def test_resolve_user_matches_by_email_only(self, fresh_db):
        """A qiita-miint principal whose email changed gets the new email's
        account: different emails are always different accounts."""
        from store.auth_store import get_user, resolve_user
        old = resolve_user("old@y.edu", principal_idx=42)
        new = resolve_user("new@y.edu", principal_idx=42)
        assert new != old
        assert get_user(old)["email"] == "old@y.edu" and get_user(old)["principal_idx"] == 42
        assert get_user(new)["principal_idx"] is None                       # 42 is already recorded elsewhere

    def test_resolve_user_never_moves_a_principal_onto_a_claimed_email(self, fresh_db):
        from store.auth_store import get_user, resolve_user
        mine = resolve_user("a@y.edu", principal_idx=1)
        theirs = resolve_user("b@y.edu", level="user")
        assert resolve_user("b@y.edu", principal_idx=1) == theirs
        assert get_user(mine)["email"] == "a@y.edu" and get_user(mine)["principal_idx"] == 1
        assert get_user(theirs)["principal_idx"] is None                    # 1 is already held

    def test_resolve_user_refuses_an_email_that_moved_between_principals(self, fresh_db):
        """qiita-miint gave the address to someone else: they must not inherit
        the first person's data. Classic logins don't carry a principal."""
        import pytest as _pytest
        from store.auth_store import PrincipalMismatch, resolve_user
        uid = resolve_user("e@y.edu", principal_idx=1)
        with _pytest.raises(PrincipalMismatch):
            resolve_user("e@y.edu", principal_idx=2)
        assert resolve_user("e@y.edu", principal_idx=1) == uid
        assert resolve_user("e@y.edu", level="user") == uid

    def test_resolve_user_first_login_race(self, fresh_db, monkeypatch):
        """Two first logins at once: the loser's INSERT hits UNIQUE and it
        re-reads the winner's row instead of failing the login."""
        from store import auth_store
        from store.db import _conn
        real_hex = auth_store.secrets.token_hex

        def _winner_inserts_first(n):
            with _conn() as conn:
                conn.execute("INSERT INTO users(user_id, email) VALUES('qwinner', 'race@y.edu')")
                conn.commit()
            return real_hex(n)
        monkeypatch.setattr(auth_store.secrets, "token_hex", _winner_inserts_first)
        assert auth_store.resolve_user("race@y.edu", level="user") == "qwinner"

    def test_purge_expired_and_old_revoked(self, fresh_db):
        from store.auth_store import purge_expired_sessions
        from store.db import _conn
        uid, _ = self._session()
        past = (datetime.utcnow() - timedelta(days=8)).isoformat() + "Z"
        future = (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z"
        with _conn() as conn:
            for h, exp, rev in [("expired", past, None), ("old_revoked", future, past),
                                ("new_revoked", future, future)]:
                conn.execute("INSERT INTO auth_sessions(session_hash, user_id, pat_encrypted, csrf_token, "
                             "absolute_expires_at, revoked_at) VALUES(?, ?, '', 'c', ?, ?)", (h, uid, exp, rev))
            conn.commit()
        purge_expired_sessions()
        with _conn() as conn:
            left = {r[0] for r in conn.execute("SELECT session_hash FROM auth_sessions")}
        assert "expired" not in left and "old_revoked" not in left and "new_revoked" in left


# ── POST /api/auth/login, in check order ─────────────────────────────────────

class TestLogin:
    def test_success_sets_cookies_and_me(self, api_client, accts):
        resp = ah.post_login(api_client, "alice@test.edu")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["email"] == "alice@test.edu" and body["level"] == "user" and body["csrf_token"]
        cookies = " ".join(resp.headers.getlist("Set-Cookie"))
        assert "qe_sid=" in cookies and "HttpOnly" in cookies and "SameSite=Lax" in cookies
        assert "qe_dev=" in cookies
        me = api_client.get("/api/auth/me").get_json()
        assert me["user_id"] == body["user_id"] and me["email"] == "alice@test.edu"
        assert me["csrf_token"] == body["csrf_token"] and me["qiita_url"]

    def test_me_anonymous_carries_qiita_url(self, api_client):
        assert api_client.get("/api/auth/me").get_json() == {
            "anonymous": True, "qiita_url": "https://qiita.ucsd.edu", "login_methods": ["classic", "miint"]}

    def test_body_too_large(self, api_client, accts):
        resp = api_client.post("/api/auth/login", data="{" + " " * 9000 + "}",
                               content_type="application/json", headers={"Origin": ah.TEST_ORIGIN})
        assert resp.status_code == 413

    def test_form_post_refused(self, api_client, accts):
        """An HTML form can't send JSON, so a cross-site form can't log anyone in."""
        resp = api_client.post("/api/auth/login", data={"email": "alice@test.edu", "password": ah.TEST_PASSWORD},
                               headers={"Origin": ah.TEST_ORIGIN})
        assert resp.status_code == 415

    @pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"},
                                         {"Origin": ah.TEST_ORIGIN, "Sec-Fetch-Site": "cross-site"}])
    def test_origin_enforced(self, api_client, accts, headers):
        body = {"envelope": ah.seal(api_client, {"email": "alice@test.edu", "password": ah.TEST_PASSWORD})}
        resp = api_client.post("/api/auth/login", json=body, headers=headers)
        assert resp.status_code == 403
        assert "qe_sid" not in " ".join(resp.headers.getlist("Set-Cookie"))

    def test_no_origin_configured_refuses(self, api_client, accts, monkeypatch):
        import config
        monkeypatch.setattr(config, "PUBLIC_ORIGIN", "")
        assert ah.post_login(api_client, "alice@test.edu").status_code == 503

    def test_allowed_dev_origin(self, api_client, accts, monkeypatch):
        import config
        monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["http://127.0.0.1:5503"])
        assert ah.post_login(api_client, "alice@test.edu", origin="http://127.0.0.1:5503",
                             headers={"Sec-Fetch-Site": "same-site"}).status_code == 200

    def test_same_site_refused_for_the_public_origin(self, api_client, accts):
        """A sibling subdomain is same-site; only a dev origin may be."""
        resp = ah.post_login(api_client, "alice@test.edu", headers={"Sec-Fetch-Site": "same-site"})
        assert resp.status_code == 403

    @pytest.mark.parametrize("email,password", [("not-an-email", ah.TEST_PASSWORD), ("alice@test.edu", "short"),
                                                ("alice@test.edu", "has space x"), ("alice@test.edu", None),
                                                ("aliKe@test.edu", ah.TEST_PASSWORD)])
    def test_bad_format_is_generic_401_and_not_counted(self, api_client, accts, email, password):
        import config
        body = {"envelope": ah.seal(api_client, {"email": email, "password": password})}
        resp = api_client.post("/api/auth/login", json=body, headers={"Origin": ah.TEST_ORIGIN})
        assert resp.status_code == 401 and resp.get_json()["error"] == "Incorrect email or password."
        assert not os.path.exists(config.LIMITER_DB_PATH) or not sqlite3.connect(
            config.LIMITER_DB_PATH).execute("SELECT 1 FROM attempts").fetchone()

    def test_unknown_email_and_wrong_password_look_the_same(self, api_client, accts, monkeypatch):
        import routes.auth_routes as ar
        burned = []
        monkeypatch.setattr(ar.accounts, "burn_check", lambda pw: burned.append(1) or False)
        unknown = ah.post_login(api_client, "nobody@test.edu")
        wrong = ah.post_login(api_client, "alice@test.edu", password="wrong-pass-1")
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.get_json() == wrong.get_json()
        assert burned == [1]          # the unknown email still paid for a bcrypt check

    def test_email_normalized(self, api_client, accts):
        resp = ah.post_login(api_client, "  Alice@TEST.edu ")
        assert resp.status_code == 200 and resp.get_json()["email"] == "alice@test.edu"

    def test_unverified_only_told_after_correct_password(self, api_client, accts):
        assert ah.post_login(api_client, "una@test.edu", password="wrong-pass-1").status_code == 401
        resp = ah.post_login(api_client, "una@test.edu")
        assert resp.status_code == 403 and resp.get_json()["unverified"] is True
        assert "qe_sid" not in " ".join(resp.headers.getlist("Set-Cookie"))

    def test_qiita_unreachable_is_503(self, api_client, accts, monkeypatch):
        import routes.auth_routes as ar

        def _down(email):
            raise ar.accounts.AccountsUnavailable("down")
        monkeypatch.setattr(ar.accounts, "fetch_account", _down)
        assert ah.post_login(api_client, "alice@test.edu").status_code == 503

    def test_bcrypt_busy_is_503_with_retry_after(self, api_client, accts, monkeypatch):
        import routes.auth_routes as ar

        def _busy(*a):
            raise ar.accounts.Busy()
        monkeypatch.setattr(ar.accounts, "check_password", _busy)
        resp = ah.post_login(api_client, "alice@test.edu")
        assert resp.status_code == 503 and resp.headers["Retry-After"] == "2"

    def test_limiter_unavailable_fails_closed(self, api_client, accts, monkeypatch, tmp_path):
        import config
        (tmp_path / "blocker").write_text("")
        monkeypatch.setattr(config, "LIMITER_DB_PATH", str(tmp_path / "blocker" / "limiter.db"))
        assert ah.post_login(api_client, "alice@test.edu").status_code == 503

    def test_chunked_body_is_capped_too(self, api_client, accts):
        """No Content-Length (chunked): the 8 KB cap still holds."""
        import io
        body = b'{"email": "alice@test.edu", "password": "' + b"x" * 9000 + b'"}'
        resp = api_client.post("/api/auth/login", input_stream=io.BytesIO(body), content_type="application/json",
                               headers={"Origin": ah.TEST_ORIGIN},
                               environ_overrides={"wsgi.input_terminated": True})
        assert resp.status_code == 413

    def test_client_ip_is_the_proxys_entry(self, _app, monkeypatch):
        import config
        import routes.auth_routes as ar
        monkeypatch.setattr(config, "CLIENT_IP_HEADER", "X-QE-Client-IP")
        for value, expected in [("203.0.113.7", "203.0.113.7"), ("6.6.6.6, 203.0.113.7", "203.0.113.7"),
                                ("not-an-ip", None), ("", None)]:
            with _app.test_request_context(headers={"X-QE-Client-IP": value}):
                assert ar._client_ip() == expected

    def test_progressive_delay_then_known_device_still_gets_in(self, api_client, accts, _app):
        # alice signed in here before: this browser now holds her device cookie.
        assert ah.post_login(api_client, "alice@test.edu").status_code == 200
        attacker = _app.test_client()
        codes = [ah.post_login(attacker, "alice@test.edu", password="guess-pass-%d" % i).status_code
                 for i in range(7)]
        assert codes[:6] == [401] * 6 and codes[6] == 429
        blocked = ah.post_login(attacker, "alice@test.edu")
        assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1
        assert ah.post_login(api_client, "alice@test.edu").status_code == 200

    def test_success_clears_the_email_counter(self, api_client, accts):
        for i in range(5):
            ah.post_login(api_client, "bob@test.edu", password="guess-pass-%d" % i)
        assert ah.post_login(api_client, "bob@test.edu").status_code == 200
        api_client.delete_cookie("qe_dev")
        for i in range(5):     # a fresh window of free attempts
            assert ah.post_login(api_client, "bob@test.edu", password="guess-pass-%d" % i).status_code == 401

    def test_relogin_replaces_prior_session(self, api_client, accts, _app):
        ah.post_login(api_client, "alice@test.edu")
        stale = _app.test_client()
        stale.set_cookie("qe_sid", api_client.get_cookie("qe_sid").value)
        assert stale.get("/api/auth/me").get_json()["email"] == "alice@test.edu"
        assert ah.post_login(api_client, "bob@test.edu").status_code == 200
        assert stale.get("/api/auth/me").get_json()["anonymous"] is True
        assert api_client.get("/api/auth/me").get_json()["email"] == "bob@test.edu"

    def test_failed_relogin_keeps_current_session(self, api_client, accts):
        ah.post_login(api_client, "alice@test.edu")
        assert ah.post_login(api_client, "bob@test.edu", password="wrong-pass-1").status_code == 401
        assert api_client.get("/api/auth/me").get_json()["email"] == "alice@test.edu"

    def test_host_prefixed_cookies_when_secure(self, api_client, accts, monkeypatch):
        import config
        monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", True)
        monkeypatch.setattr(config, "SESSION_COOKIE_NAME", "__Host-qe_sid")
        monkeypatch.setattr(config, "DEVICE_COOKIE_NAME", "__Host-qe_dev")
        cookies = ah.post_login(api_client, "alice@test.edu").headers.getlist("Set-Cookie")
        for name in ("__Host-qe_sid=", "__Host-qe_dev="):
            c = next(c for c in cookies if c.startswith(name))
            assert "Secure" in c and "Path=/" in c and "Domain" not in c

    def test_password_never_logged_or_stored(self, api_client, accts, auth_db_path, caplog):
        import config
        secret = "Unique-Secret-Pw-91"
        caplog.set_level(logging.DEBUG)
        ah.post_login(api_client, "alice@test.edu", password=secret)
        ah.post_login(api_client, "nobody@test.edu", password=secret)
        assert secret not in caplog.text
        for path in (auth_db_path, auth_db_path + "-wal", auth_db_path + "-journal",
                     config.LIMITER_DB_PATH, config.LIMITER_DB_PATH + "-journal"):
            if os.path.exists(path):
                assert secret.encode() not in open(path, "rb").read()

    def test_legacy_claim_routes_are_gone(self, api_client):
        assert api_client.post("/api/auth/claim-default", json={}).status_code == 404
        assert api_client.get("/api/auth/legacy-default").status_code == 404

    def test_logout_and_default_deny(self, api_client, monkeypatch):
        assert api_client.get("/api/projects").status_code == 401
        headers = ah.login(api_client, monkeypatch, "alice@test.edu")
        assert api_client.post("/api/projects", json={"name": "x"}).status_code == 403          # no CSRF
        assert api_client.post("/api/projects", json={"name": "x"},
                               headers={"X-CSRF-Token": "nope"}).status_code == 403
        assert api_client.post("/api/projects", json={"name": "x"}, headers=headers).status_code == 200
        assert api_client.post("/api/auth/logout", headers=headers).status_code == 200
        assert api_client.get("/api/auth/me").get_json()["anonymous"] is True
        assert api_client.get("/api/projects").status_code == 401


# ── Sessions follow the Qiita account (helpers/auth_middleware.py) ──────────

class TestRecheck:
    def _login(self, api_client, monkeypatch, **kw):
        ah.login(api_client, monkeypatch, "alice@test.edu", **kw)

    def test_recent_session_not_rechecked(self, api_client, monkeypatch, auth_db_path):
        import routes.auth_routes as ar
        self._login(api_client, monkeypatch)
        calls = []
        monkeypatch.setattr(ar.accounts, "fetch_account", lambda e: calls.append(e))
        assert api_client.get("/api/projects").status_code == 200
        assert calls == []

    def test_stale_valid_session_is_marked_verified(self, api_client, monkeypatch, auth_db_path):
        self._login(api_client, monkeypatch)
        _age_verification(auth_db_path, 600)
        assert api_client.get("/api/projects").status_code == 200
        sess = _sessions(auth_db_path, "revoked_at IS NULL ORDER BY rowid DESC")[0]
        age = datetime.utcnow() - datetime.fromisoformat(sess["last_verified_at"].rstrip("Z"))
        assert age.total_seconds() < 60

    @pytest.mark.parametrize("change", ["password", "deleted", "unverified"])
    def test_account_change_on_qiita_ends_session(self, api_client, monkeypatch, auth_db_path, change):
        self._login(api_client, monkeypatch)
        if change == "password":
            ah.fake_accounts(monkeypatch, ah.account("alice@test.edu", password="new-password-2"))
        elif change == "deleted":
            ah.fake_accounts(monkeypatch)
        else:
            ah.fake_accounts(monkeypatch, ah.account("alice@test.edu", level="unverified", level_id=5))
        _age_verification(auth_db_path, 600)
        assert api_client.get("/api/projects").status_code == 401
        assert api_client.get("/api/auth/me").get_json()["anonymous"] is True

    def test_session_without_fingerprint_ends(self, api_client, monkeypatch, auth_db_path):
        self._login(api_client, monkeypatch)
        conn = sqlite3.connect(auth_db_path)
        conn.execute("UPDATE auth_sessions SET password_fp = NULL")
        conn.commit()
        conn.close()
        _age_verification(auth_db_path, 600)
        assert api_client.get("/api/projects").status_code == 401

    def test_qiita_down_grace_then_503_then_recovers(self, api_client, monkeypatch, auth_db_path):
        import routes.auth_routes as ar
        self._login(api_client, monkeypatch)
        working = ar.accounts.fetch_account

        def _down(email):
            raise ar.accounts.AccountsUnavailable("down")
        monkeypatch.setattr(ar.accounts, "fetch_account", _down)
        _age_verification(auth_db_path, 600)
        assert api_client.get("/api/projects").status_code == 200        # within the hour
        _age_verification(auth_db_path, 3700)
        resp = api_client.get("/api/projects")
        assert resp.status_code == 503 and resp.headers["Retry-After"]
        assert api_client.get("/api/auth/me").status_code == 503
        monkeypatch.setattr(ar.accounts, "fetch_account", working)
        assert api_client.get("/api/projects").status_code == 200        # not revoked meanwhile

    def test_logout_still_works_when_qiita_is_down(self, api_client, monkeypatch, auth_db_path, _app):
        import routes.auth_routes as ar
        headers = ah.login(api_client, monkeypatch, "alice@test.edu")
        token = api_client.get_cookie("qe_sid").value
        working = ar.accounts.fetch_account

        def _down(email):
            raise ar.accounts.AccountsUnavailable("down")
        monkeypatch.setattr(ar.accounts, "fetch_account", _down)
        _age_verification(auth_db_path, 3700)
        assert api_client.post("/api/auth/logout").status_code == 403                # still needs CSRF
        assert api_client.post("/api/auth/logout", headers=headers).status_code == 200
        monkeypatch.setattr(ar.accounts, "fetch_account", working)
        replay = _app.test_client()
        replay.set_cookie("qe_sid", token)
        assert replay.get("/api/auth/me").get_json()["anonymous"] is True         # revoked, not just forgotten


# ── Cross-user isolation (the core security goal) ────────────────────────────

class TestCrossUserIsolation:
    def _as(self, client, monkeypatch, who):
        headers = ah.login(client, monkeypatch, f"{who}@test.edu")
        return headers, client.get("/api/auth/me").get_json()["user_id"]

    def test_projects_isolated(self, api_client, monkeypatch):
        h_a, uid_a = self._as(api_client, monkeypatch, "iso-a")
        proj = api_client.post("/api/projects", json={"name": "A's project"}, headers=h_a).get_json()
        assert proj["user_id"] == uid_a
        h_b, _ = self._as(api_client, monkeypatch, "iso-b")
        assert api_client.get(f"/api/projects/{proj['project_id']}", headers=h_b).status_code == 404
        assert api_client.get("/api/projects", headers=h_b).get_json()["projects"] == []
        api_client.delete(f"/api/projects/{proj['project_id']}", headers=h_b)
        h_a2, uid_a2 = self._as(api_client, monkeypatch, "iso-a")
        assert uid_a2 == uid_a
        assert api_client.get(f"/api/projects/{proj['project_id']}", headers=h_a2).status_code == 200

    def test_global_chats_isolated(self, api_client, monkeypatch):
        h_a, _ = self._as(api_client, monkeypatch, "iso-c")
        chat = api_client.post("/api/global-chats", json={"title": "A chat"}, headers=h_a).get_json()
        h_b, _ = self._as(api_client, monkeypatch, "iso-d")
        assert api_client.get(f"/api/global-chats/{chat['chat_id']}", headers=h_b).status_code == 404

    def test_merge_workspace_study_mutations_owner_scoped(self, api_client, monkeypatch):
        import routes.merge_routes as mr
        monkeypatch.setattr(mr, "is_study_public", lambda sid: True)   # so the 404s below are ownership
        h_a, _ = self._as(api_client, monkeypatch, "iso-e")
        ws = api_client.post("/api/merge-workspaces", json={"name": "WS A"}, headers=h_a).get_json()
        h_b, _ = self._as(api_client, monkeypatch, "iso-f")
        base = f"/api/merge-workspaces/{ws['workspace_id']}"
        assert api_client.post(f"{base}/studies", json={"study_id": 111}, headers=h_b).status_code == 404
        assert api_client.delete(f"{base}/studies/111", headers=h_b).status_code == 404
        assert api_client.patch(f"{base}/studies/111", json={"chosen_artifact_ids": [1]},
                                headers=h_b).status_code == 404
        assert api_client.get(f"{base}/jobs", headers=h_b).get_json() == []

    def test_user_id_spoofing_ignored(self, api_client, monkeypatch):
        _, uid_a = self._as(api_client, monkeypatch, "iso-g")
        h_b, uid_b = self._as(api_client, monkeypatch, "iso-h")
        resp = api_client.post(f"/api/projects?user_id={uid_a}", json={"name": "spoof", "user_id": uid_a},
                               headers=h_b)
        assert resp.get_json()["user_id"] == uid_b


# ── TKT-042: the shared Anthropic key can't be set from the browser ──────────

class TestSettingsLocked:
    def test_post_settings_removed(self, api_client, monkeypatch):
        headers = ah.login(api_client, monkeypatch, "keys@test.edu")
        resp = api_client.post("/api/settings", json={"anthropic_api_key": "sk-ant-mine"}, headers=headers)
        assert resp.status_code == 405

    def test_get_settings_reports_server_key(self, api_client, monkeypatch):
        import routes.study_routes as sr
        ah.login(api_client, monkeypatch, "keys@test.edu")
        monkeypatch.setattr(sr, "ANTHROPIC_API_KEY", "")
        assert api_client.get("/api/settings").get_json() == {"anthropic_key_set": False}
        monkeypatch.setattr(sr, "ANTHROPIC_API_KEY", "sk-ant-env")
        assert api_client.get("/api/settings").get_json() == {"anthropic_key_set": True}
