"""Signing a test client in through the real routes — POST /api/auth/login with
classic Qiita's account table faked (no Postgres), POST /api/auth/connect with
qiita-miint's whoami faked (no network) — so no route-test fixture reaches
into the session store directly."""
import bcrypt

TEST_ORIGIN = "http://qe.test"
TEST_PASSWORD = "correct-horse-1"
_hash = {}


def password_hash(password: str = TEST_PASSWORD) -> str:
    """A real bcrypt hash at the lowest cost (fast), cached per password."""
    if password not in _hash:
        _hash[password] = bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode()
    return _hash[password]


def configure(config):
    """Both logins on, plain-HTTP cookies and a known origin, for a module's
    freshly imported app."""
    config.LOGIN_METHODS = ["classic", "miint"]
    config.ALLOWED_ORIGINS = []
    config.PUBLIC_ORIGIN = TEST_ORIGIN
    config.CLIENT_IP_HEADER = ""
    config.SESSION_COOKIE_SECURE = False
    config.SESSION_COOKIE_NAME = "qe_sid"
    config.DEVICE_COOKIE_NAME = "qe_dev"


def account(email, *, password=TEST_PASSWORD, level="user", level_id=3):
    from helpers.qiita_accounts import Account
    return Account(email=email, password_hash=password_hash(password), level_id=level_id, level=level)


def fake_accounts(monkeypatch, *accts):
    """Make classic Qiita's table hold exactly these accounts. Patched on the
    module the running app imported (routes.auth_routes.accounts), which the
    middleware's re-check shares."""
    import routes.auth_routes as auth_routes
    table = {a.email: a for a in accts}
    monkeypatch.setattr(auth_routes.accounts, "fetch_account", lambda email: table.get(email))
    return table


def post_login(client, email, password=TEST_PASSWORD, origin=TEST_ORIGIN, **kw):
    headers = {"Origin": origin} if origin else {}
    headers.update(kw.pop("headers", {}))
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=headers, **kw)


def login(client, monkeypatch, email, **account_kw):
    """Sign `client` in as a verified Qiita user; returns the CSRF headers."""
    fake_accounts(monkeypatch, account(email, **account_kw))
    resp = post_login(client, email)
    assert resp.status_code == 200, resp.get_json()
    return {"X-CSRF-Token": resp.get_json()["csrf_token"]}


# ── qiita-miint ──────────────────────────────────────────────────────────────

def token_for(n) -> str:
    """A distinct, validly shaped qk_ token (qk_ + 43 URL-safe characters)."""
    return "qk_" + f"{n:043d}"


def human(principal_idx, email):
    return {"kind": "human", "principal_idx": principal_idx, "email": email,
            "system_role": "user", "scopes": [], "profile_complete": True}


def fake_whoami(monkeypatch, identities):
    """qiita-miint answers whoami from {token: identity dict | WhoAmIResult};
    any other token is a definitive 401. Returns the list of tokens asked about."""
    import routes.auth_routes as auth_routes
    from helpers.qiita_client import WhoAmIResult
    calls = []

    def _whoami(token):
        calls.append(token)
        answer = identities.get(token)
        if answer is None:
            return WhoAmIResult(ok=False, transient_error=False)
        return answer if isinstance(answer, WhoAmIResult) else WhoAmIResult(ok=True, identity=answer)
    monkeypatch.setattr(auth_routes, "whoami", _whoami)
    return calls


def post_connect(client, token, origin=TEST_ORIGIN, **kw):
    headers = {"Origin": origin} if origin else {}
    headers.update(kw.pop("headers", {}))
    return client.post("/api/auth/connect", json={"token": token}, headers=headers, **kw)


def connect(client, monkeypatch, email, principal_idx):
    """Sign `client` in through qiita-miint; returns the CSRF headers."""
    token = token_for(principal_idx)
    fake_whoami(monkeypatch, {token: human(principal_idx, email)})
    resp = post_connect(client, token)
    assert resp.status_code == 200, resp.get_json()
    return {"X-CSRF-Token": resp.get_json()["csrf_token"]}
