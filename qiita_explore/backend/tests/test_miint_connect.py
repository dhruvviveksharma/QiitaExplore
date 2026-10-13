"""qiita-miint sign-in beside classic Qiita: the whoami client, POST
/api/auth/connect and GET /api/auth/login-url, one account per email across
both logins, the per-method switches, and how each kind of session is kept.
whoami is faked (tests/auth_helpers.py); no network."""
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

from . import auth_helpers as ah
from .conftest import stub_qiita_db_and_core


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    return str(tmp_path_factory.mktemp("miint_connect") / "test.db")


@pytest.fixture(scope="module")
def _app(db_path):
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = db_path
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
def client(_app):
    return _app.test_client()


def _me(client):
    return client.get("/api/auth/me").get_json()


# ── Unit: helpers/qiita_client.whoami (httpx faked) ─────────────────────────

class _FakeHttpxResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class TestWhoami:
    @pytest.fixture(autouse=True)
    def _fresh_breaker(self, monkeypatch):
        from helpers import qiita_client
        monkeypatch.setattr(qiita_client, "_breaker", {"fails": 0, "open_until": 0.0})

    def _call(self, monkeypatch, response):
        import httpx
        from helpers import qiita_client
        get = response if callable(response) else (lambda *a, **k: response)
        monkeypatch.setattr(httpx, "get", get)
        return qiita_client.whoami("qk_x")

    def test_human_accepted(self, monkeypatch):
        r = self._call(monkeypatch, _FakeHttpxResponse(200, {"kind": "human", "principal_idx": 1}))
        assert r.ok and r.identity["principal_idx"] == 1

    @pytest.mark.parametrize("body", [{"kind": "service", "principal_idx": 7}, {"kind": "anonymous"}])
    def test_non_human_rejected_definitively(self, monkeypatch, body):
        r = self._call(monkeypatch, _FakeHttpxResponse(200, body))
        assert not r.ok and not r.transient_error

    def test_401_is_definitive(self, monkeypatch):
        r = self._call(monkeypatch, _FakeHttpxResponse(401, {}))
        assert not r.ok and not r.transient_error

    @pytest.mark.parametrize("body", [[], None, "human", 7])
    def test_non_object_body_is_transient(self, monkeypatch, body):
        r = self._call(monkeypatch, _FakeHttpxResponse(200, body))
        assert not r.ok and r.transient_error

    @pytest.mark.parametrize("status", [403, 404, 302, 400, 429, 503])
    def test_anything_else_is_transient(self, monkeypatch, status):
        r = self._call(monkeypatch, _FakeHttpxResponse(status, {}))
        assert not r.ok and r.transient_error

    def test_connection_error_is_transient(self, monkeypatch):
        import httpx

        def _raise(*a, **k):
            raise httpx.ConnectError("refused")
        r = self._call(monkeypatch, _raise)
        assert not r.ok and r.transient_error

    def test_breaker_fails_fast_after_three_transients(self, monkeypatch):
        import httpx
        from helpers import qiita_client
        calls = []
        monkeypatch.setattr(httpx, "get", lambda *a, **k: calls.append(1) or _FakeHttpxResponse(503, {}))
        for _ in range(3):
            assert qiita_client.whoami("qk_x").transient_error
        assert qiita_client.whoami("qk_x").transient_error and len(calls) == 3     # open: no call made

    def test_busy_when_this_worker_is_mid_call(self, monkeypatch):
        from helpers import qiita_client
        monkeypatch.setattr(qiita_client, "_SLOT_WAIT_SECONDS", 0.01)
        assert qiita_client._slot.acquire(timeout=1)
        try:
            with pytest.raises(qiita_client.Busy):
                qiita_client.whoami("qk_x")
        finally:
            qiita_client._slot.release()


# ── POST /api/auth/connect ───────────────────────────────────────────────────

class TestConnect:
    def test_success(self, client, monkeypatch):
        headers = ah.connect(client, monkeypatch, "Miint.User@UCSD.edu", 501)
        me = _me(client)
        assert me["email"] == "miint.user@ucsd.edu" and me["user_id"].startswith("q")
        assert me["csrf_token"] == headers["X-CSRF-Token"]

    @pytest.mark.parametrize("token", ["", "not-a-token", "qk_short", "qk_" + "a" * 200, "qk_" + "a" * 40 + "!", None, 5])
    def test_bad_token_shape_never_reaches_whoami(self, client, monkeypatch, token):
        calls = ah.fake_whoami(monkeypatch, {})
        body = {"envelope": ah.seal(client, {"token": token})}
        resp = client.post("/api/auth/connect", json=body, headers={"Origin": ah.TEST_ORIGIN})
        assert resp.status_code == 401 and calls == []

    def test_unknown_token_401_and_transient_503(self, client, monkeypatch):
        from helpers.qiita_client import WhoAmIResult
        ah.fake_whoami(monkeypatch, {ah.token_for(2): WhoAmIResult(ok=False, transient_error=True)})
        assert ah.post_connect(client, ah.token_for(1)).status_code == 401
        assert ah.post_connect(client, ah.token_for(2)).status_code == 503

    def test_front_checks_shared_with_login(self, client, monkeypatch):
        calls = ah.fake_whoami(monkeypatch, {ah.token_for(1): ah.human(1, "a@x.edu")})
        assert ah.post_connect(client, ah.token_for(1), origin=None).status_code == 403
        assert ah.post_connect(client, ah.token_for(1), origin="https://evil.example").status_code == 403
        form = client.post("/api/auth/connect", data={"token": ah.token_for(1)}, headers={"Origin": ah.TEST_ORIGIN})
        assert form.status_code == 415
        assert calls == []

    @pytest.mark.parametrize("email", ["kiKr@x.edu", "kir@x.edu\u0000", None, "no-at-sign"])
    def test_unusable_email_refused(self, client, monkeypatch, email):
        """Email is the link to an account, so qiita-miint emails pass classic's
        own rule: a Kelvin-sign lookalike must never land on kir@x.edu."""
        ah.fake_whoami(monkeypatch, {ah.token_for(9): ah.human(9, email)})
        assert ah.post_connect(client, ah.token_for(9)).status_code == 401

    @pytest.mark.parametrize("email", [" pad@x.edu", "pad@x.edu\u3000", "pad@x.edu\n"])
    def test_padded_email_refused_not_trimmed(self, client, monkeypatch, email):
        ah.fake_whoami(monkeypatch, {ah.token_for(8): ah.human(8, email)})
        assert ah.post_connect(client, ah.token_for(8)).status_code == 401

    def test_email_moved_to_another_principal_is_refused(self, client, monkeypatch):
        """qiita-miint gave the address to a different account: 409, not the
        first person's data. The original principal still gets in."""
        h = ah.connect(client, monkeypatch, "moved@x.edu", 901)
        client.post("/api/projects", json={"name": "first person's"}, headers=h)
        client.post("/api/auth/logout", headers=h)
        ah.fake_whoami(monkeypatch, {ah.token_for(902): ah.human(902, "moved@x.edu")})
        resp = ah.post_connect(client, ah.token_for(902))
        assert resp.status_code == 409 and _me(client).get("anonymous") is True
        ah.connect(client, monkeypatch, "moved@x.edu", 901)
        assert _me(client)["email"] == "moved@x.edu"

    def test_busy_whoami_is_503(self, client, monkeypatch):
        import routes.auth_routes as ar

        def _busy(token):
            raise ar.qiita_client.Busy()        # the class the route catches
        monkeypatch.setattr(ar, "whoami", _busy)
        resp = ah.post_connect(client, ah.token_for(5))
        assert resp.status_code == 503 and resp.headers["Retry-After"]

    @pytest.mark.parametrize("principal", ["3", True, None, 3.0])
    def test_non_integer_principal_is_502(self, client, monkeypatch, principal):
        ah.fake_whoami(monkeypatch, {ah.token_for(3): {**ah.human(3, "p@x.edu"), "principal_idx": principal}})
        assert ah.post_connect(client, ah.token_for(3)).status_code == 502

    def test_failed_connect_keeps_current_session(self, client, monkeypatch):
        ah.connect(client, monkeypatch, "keep@x.edu", 601)
        assert ah.post_connect(client, ah.token_for(999)).status_code == 401
        assert _me(client)["email"] == "keep@x.edu"

    def test_token_never_stored(self, client, monkeypatch, db_path):
        ah.connect(client, monkeypatch, "store@x.edu", 602)
        token = ah.token_for(602).encode()
        for path in (db_path, db_path + "-wal", db_path + "-journal"):
            if os.path.exists(path):
                assert token not in open(path, "rb").read()


class TestLoginUrl:
    def test_plain_and_through_loginrocket(self, client, monkeypatch):
        import config
        from urllib.parse import parse_qs, urlparse
        monkeypatch.setattr(config, "QIITA_PUBLIC_LOGIN_URL", "https://miint.example")
        monkeypatch.setattr(config, "QIITA_LOGINROCKET_URL", "")
        assert client.get("/api/auth/login-url").get_json() == {"url": "https://miint.example/api/v1/auth/login"}
        monkeypatch.setattr(config, "QIITA_LOGINROCKET_URL", "https://realm.e2.loginrocket.com")
        url = client.get("/api/auth/login-url").get_json()["url"]
        assert url.startswith("https://realm.e2.loginrocket.com/logout?redirect_uri=") and url.count("?") == 1
        assert parse_qs(urlparse(url).query)["redirect_uri"][0] == "https://miint.example/api/v1/auth/login"


# ── One account per email, whichever login ──────────────────────────────────

class TestOneAccountPerEmail:
    def test_classic_then_miint_same_email_same_data(self, client, monkeypatch):
        h = ah.login(client, monkeypatch, "both@ucsd.edu")
        uid = _me(client)["user_id"]
        proj = client.post("/api/projects", json={"name": "made via classic"}, headers=h).get_json()
        client.post("/api/auth/logout", headers=h)
        h2 = ah.connect(client, monkeypatch, "Both@UCSD.edu", 701)
        assert _me(client)["user_id"] == uid
        names = [p["name"] for p in client.get("/api/projects", headers=h2).get_json()["projects"]]
        assert "made via classic" in names
        assert client.get(f"/api/projects/{proj['project_id']}", headers=h2).status_code == 200

    def test_miint_then_classic_same_email_same_account(self, client, monkeypatch):
        ah.connect(client, monkeypatch, "first-miint@ucsd.edu", 702)
        uid = _me(client)["user_id"]
        ah.login(client, monkeypatch, "first-miint@ucsd.edu")
        me = _me(client)
        assert me["user_id"] == uid and me["level"] == "user"

    def test_different_emails_stay_separate(self, client, monkeypatch):
        h = ah.login(client, monkeypatch, "d4sharma@ucsd.edu")
        client.post("/api/projects", json={"name": "ucsd only"}, headers=h)
        uid = _me(client)["user_id"]
        h2 = ah.connect(client, monkeypatch, "d4sharma@eng.ucsd.edu", 703)     # same name, other domain
        assert _me(client)["user_id"] != uid
        assert client.get("/api/projects", headers=h2).get_json()["projects"] == []


# ── Switches ─────────────────────────────────────────────────────────────────

class TestLoginMethodSwitches:
    def test_switching_a_method_off_ends_its_sessions(self, client, monkeypatch):
        import config
        h = ah.connect(client, monkeypatch, "switch@x.edu", 910)
        monkeypatch.setattr(config, "LOGIN_METHODS", ["classic"])
        assert client.get("/api/projects", headers=h).status_code == 401
        monkeypatch.setattr(config, "LOGIN_METHODS", ["classic", "miint"])
        assert client.get("/api/projects", headers=h).status_code == 401          # ended, not just hidden

    def test_miint_off(self, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "LOGIN_METHODS", ["classic"])
        calls = ah.fake_whoami(monkeypatch, {ah.token_for(1): ah.human(1, "a@x.edu")})
        assert ah.post_connect(client, ah.token_for(1)).status_code == 404
        assert client.get("/api/auth/login-url").status_code == 404
        assert calls == []
        assert _me(client)["login_methods"] == ["classic"]

    def test_classic_off(self, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "LOGIN_METHODS", ["miint"])
        ah.fake_accounts(monkeypatch, ah.account("a@x.edu"))
        assert ah.post_login(client, "a@x.edu").status_code == 404
        assert _me(client)["login_methods"] == ["miint"]

    @staticmethod
    def _import_config(**env):
        import subprocess
        from pathlib import Path
        # Present-but-empty, not absent: config's load_dotenv never overrides a set
        # variable, so a developer's .env can't leak into these checks.
        full = {**os.environ, "QIITA_CONTROL_PLANE_URL": "", "QIITA_CONTROL_PLANE_ALLOW_HTTP": "",
                "QIITA_EXPLORE_LOGIN_METHODS": "classic,miint"}
        full.update(env)
        return subprocess.run([sys.executable, "-c", "import config"], env=full, capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parents[1])

    @pytest.mark.parametrize("value", ["", "classic,ldap", " , "])
    def test_bad_setting_fails_at_import(self, value):
        """With no usable method nobody could sign in, so the backend won't start."""
        r = self._import_config(QIITA_EXPLORE_LOGIN_METHODS=value, QIITA_CONTROL_PLANE_URL="https://cp.test")
        assert r.returncode != 0 and "QIITA_EXPLORE_LOGIN_METHODS" in r.stderr

    def test_miint_needs_an_explicit_https_control_plane(self):
        """Whatever answers whoami can open any account, so no silent loopback
        default and no plain HTTP unless deliberately allowed."""
        assert "QIITA_CONTROL_PLANE_URL must be set" in self._import_config().stderr
        assert "must be https" in self._import_config(QIITA_CONTROL_PLANE_URL="http://127.0.0.1:18080").stderr
        assert self._import_config(QIITA_CONTROL_PLANE_URL="http://127.0.0.1:18080",
                                   QIITA_CONTROL_PLANE_ALLOW_HTTP="true").returncode == 0
        assert self._import_config(QIITA_CONTROL_PLANE_URL="https://cp.test").returncode == 0
        assert self._import_config(QIITA_EXPLORE_LOGIN_METHODS="classic").returncode == 0


# ── Sessions: classic ones follow Qiita, qiita-miint ones don't ──────────────

def _age(db_path, seconds):
    then = (datetime.utcnow() - timedelta(seconds=seconds)).isoformat() + "Z"
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE auth_sessions SET last_verified_at = ? WHERE revoked_at IS NULL", (then,))
    conn.commit()
    conn.close()


class TestSessionsPerMethod:
    def test_miint_session_is_never_rechecked(self, client, monkeypatch, db_path):
        import routes.auth_routes as ar
        h = ah.connect(client, monkeypatch, "nocheck@x.edu", 801)
        asked = []
        monkeypatch.setattr(ar.accounts, "fetch_account", lambda e: asked.append(e))   # "no classic account"
        _age(db_path, 7200)
        assert client.get("/api/projects", headers=h).status_code == 200
        assert asked == []

    def test_legacy_paste_session_survives(self, client, db_path, _app):
        """Production's existing qiita-miint sessions (source 'paste') stay valid."""
        import routes.auth_routes as ar                 # the app's own store bindings
        uid = ar.resolve_user("legacy@x.edu", principal_idx=802)
        raw, _ = ar.create_session(user_id=uid, source="paste", password_fp=None)
        legacy = _app.test_client()
        legacy.set_cookie("qe_sid", raw)
        _age(db_path, 7200)
        assert legacy.get("/api/auth/me").get_json()["user_id"] == uid

    def test_classic_session_still_rechecked(self, client, monkeypatch, db_path):
        ah.login(client, monkeypatch, "classic-check@x.edu")
        ah.fake_accounts(monkeypatch, ah.account("classic-check@x.edu", password="changed-pass-2"))
        _age(db_path, 600)
        assert client.get("/api/projects").status_code == 401

    def test_password_change_ends_the_accounts_miint_sessions_too(self, _app, monkeypatch, db_path):
        miint, classic = _app.test_client(), _app.test_client()
        hm = ah.connect(miint, monkeypatch, "everywhere@x.edu", 920)
        ah.login(classic, monkeypatch, "everywhere@x.edu")
        assert _me(miint)["user_id"] == _me(classic)["user_id"]
        ah.fake_accounts(monkeypatch, ah.account("everywhere@x.edu", password="changed-pass-2"))
        _age(db_path, 600)
        assert classic.get("/api/projects").status_code == 401                    # the re-check ends...
        assert miint.get("/api/projects", headers=hm).status_code == 401          # ...every session
