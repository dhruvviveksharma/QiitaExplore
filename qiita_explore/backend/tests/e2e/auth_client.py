"""Authenticated HTTP client for e2e tests.

Every non-auth endpoint is default-deny (helpers/auth_middleware.py):
GET requires the `qe_sid` session cookie; POST/PUT/PATCH/DELETE additionally
require a matching `X-CSRF-Token` header. This client logs in once via
POST /api/auth/login with a real classic-Qiita account (QIITA_TEST_EMAIL /
QIITA_TEST_PASSWORD) and carries the resulting cookie + CSRF token on every
subsequent request. The password is only ever sent in that one request body.
"""
import os

import requests


class AuthError(RuntimeError):
    """Login or an authenticated call failed outright."""


def login_request(session, base_url, email, password, origin, timeout=30):
    return session.post(f"{base_url.rstrip('/')}/api/auth/login",
                        json={"email": email, "password": password},
                        headers={"Origin": origin}, timeout=timeout)


class AuthedClient:
    def __init__(self, base_url, email=None, password=None, origin=None, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.user_id = None
        self.csrf_token = None
        self._login(email, password, origin)

    def _login(self, email, password, origin):
        email = email or os.environ.get("QIITA_TEST_EMAIL")
        password = password or os.environ.get("QIITA_TEST_PASSWORD")
        if not email or not password:
            raise AuthError(
                "QIITA_TEST_EMAIL / QIITA_TEST_PASSWORD are not set — export the "
                "classic-Qiita account this suite should run as."
            )
        # /api/auth/login requires Origin to equal the backend's
        # QIITA_EXPLORE_PUBLIC_ORIGIN or one of QIITA_EXPLORE_ALLOWED_ORIGINS.
        origin = origin or os.environ.get("QIITA_TEST_ORIGIN", "http://localhost:5503")
        resp = login_request(self.session, self.base_url, email, password, origin, self.timeout)
        if resp.status_code != 200:
            raise AuthError(
                f"POST /api/auth/login failed ({resp.status_code}): {resp.text[:300]}"
            )
        body = resp.json()
        self.csrf_token = body.get("csrf_token")
        self.user_id = body.get("user_id")
        if not self.csrf_token:
            raise AuthError(f"/api/auth/login did not return a csrf_token: {body}")

    def session_cookie(self):
        """The session cookie's value (named __Host-qe_sid when the backend sets Secure)."""
        jar = self.session.cookies.get_dict()
        return jar.get("__Host-qe_sid") or jar.get("qe_sid")

    def _headers(self, extra=None):
        headers = {"X-CSRF-Token": self.csrf_token}
        if extra:
            headers.update(extra)
        return headers

    def get(self, path, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(f"{self.base_url}{path}", **kwargs)

    def post(self, path, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        headers = self._headers(kwargs.pop("headers", None))
        return self.session.post(f"{self.base_url}{path}", headers=headers, **kwargs)

    def delete(self, path, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        headers = self._headers(kwargs.pop("headers", None))
        return self.session.delete(f"{self.base_url}{path}", headers=headers, **kwargs)

    def stream_post(self, path, **kwargs):
        """POST expecting an SSE stream response; caller iterates resp.iter_lines()."""
        kwargs.setdefault("timeout", self.timeout)
        headers = self._headers(kwargs.pop("headers", None))
        kwargs["stream"] = True
        return self.session.post(f"{self.base_url}{path}", headers=headers, **kwargs)

    def logout(self):
        self.post("/api/auth/logout")
