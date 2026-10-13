"""Signing a test client in through the real POST /api/auth/login, with classic
Qiita's account table faked (no Postgres): one helper for every route-test
fixture, so none of them reaches into the session store directly."""
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
    """Plain-HTTP cookies and a known origin, for a module's freshly imported app."""
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
