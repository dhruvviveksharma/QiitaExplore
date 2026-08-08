"""Authenticated HTTP client for e2e tests.

Every non-auth endpoint is default-deny (helpers/auth_middleware.py):
GET requires the `qe_sid` session cookie; POST/PUT/PATCH/DELETE additionally
require a matching `X-CSRF-Token` header. This client logs in once via
POST /api/auth/connect with a real Qiita PAT and carries the resulting
cookie + CSRF token on every subsequent request.
"""
import os

import requests


class AuthError(RuntimeError):
    """Login or an authenticated call failed outright."""


class AuthedClient:
    def __init__(self, base_url, pat=None, origin=None, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.user_id = None
        self.csrf_token = None
        self._login(pat, origin)

    def _login(self, pat, origin):
        pat = pat or os.environ.get("QIITA_TEST_PAT")
        if not pat:
            raise AuthError(
                "QIITA_TEST_PAT is not set — export a valid Qiita personal "
                "access token for the account this suite should run as."
            )
        # /api/auth/connect enforces an exact-Origin allowlist when
        # QIITA_EXPLORE_ALLOWED_ORIGINS is configured (auth_routes.py:39-46).
        origin = origin or os.environ.get("QIITA_TEST_ORIGIN", "http://localhost:5503")
        resp = self.session.post(
            f"{self.base_url}/api/auth/connect",
            json={"token": pat},
            headers={"Origin": origin},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise AuthError(
                f"POST /api/auth/connect failed ({resp.status_code}): {resp.text[:300]}"
            )
        body = resp.json()
        self.csrf_token = body.get("csrf_token")
        self.user_id = body.get("user_id")
        if not self.csrf_token:
            raise AuthError(f"/api/auth/connect did not return a csrf_token: {body}")

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
