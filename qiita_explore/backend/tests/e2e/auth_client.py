"""Authenticated HTTP client for e2e tests.

Every non-auth endpoint is default-deny (helpers/auth_middleware.py):
GET requires the `qe_sid` session cookie; POST/PUT/PATCH/DELETE additionally
require a matching `X-CSRF-Token` header. This client logs in once via
POST /api/auth/login with a real classic-Qiita account (QIITA_TEST_EMAIL /
QIITA_TEST_PASSWORD) and carries the resulting cookie + CSRF token on every
subsequent request. The password is only ever sent in that one request body,
sealed to the backend's key the way the browser does it (helpers/login_crypto.py).
"""
import base64
import json
import os

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class AuthError(RuntimeError):
    """Login or an authenticated call failed outright."""


def seal(session, base_url, payload, timeout=30):
    """The envelope the browser sends (frontend/js/auth.js sealForServer)."""
    info = session.get(f"{base_url.rstrip('/')}/api/auth/login-key", timeout=timeout).json()
    public = serialization.load_der_public_key(base64.b64decode(info["public_key"]))
    aes_key, iv = AESGCM.generate_key(256), os.urandom(12)
    plaintext = json.dumps(payload).ljust(1536).encode()          # padded, as the browser does
    ciphertext = AESGCM(aes_key).encrypt(iv, plaintext, info["challenge"].encode())
    wrapped = public.encrypt(aes_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(),
                                                   label=None))
    b64 = lambda raw: base64.b64encode(raw).decode()   # noqa: E731
    return {"key_id": info["key_id"], "challenge": info["challenge"],
            "wrapped_key": b64(wrapped), "iv": b64(iv), "ciphertext": b64(ciphertext)}


def login_request(session, base_url, email, password, origin, timeout=30):
    envelope = seal(session, base_url, {"email": email, "password": password}, timeout)
    return session.post(f"{base_url.rstrip('/')}/api/auth/login", json={"envelope": envelope},
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
