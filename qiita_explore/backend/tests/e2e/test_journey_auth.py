"""E2E tests for the login journey: POST /api/auth/connect, session cookie +
CSRF issuance, CSRF enforcement on writes, and logout revocation.

These tests deliberately avoid logging out or otherwise invalidating the
session-scoped `client` fixture — that session is shared by every other test
module in the run. Anything that needs its own session lifecycle constructs a
fresh AuthedClient instead.
"""
import pytest
import requests

from auth_client import AuthedClient


@pytest.mark.e2e
class TestLogin:
    def test_connect_sets_cookie_and_csrf(self, backend):
        fresh = AuthedClient(backend)
        assert fresh.user_id
        assert fresh.csrf_token
        assert "qe_sid" in fresh.session.cookies.get_dict()

    def test_me_returns_identity_for_logged_in_session(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        body = r.json()
        assert body.get("user_id") == client.user_id
        assert body.get("csrf_token") == client.csrf_token
        assert body.get("anonymous") is not True

    def test_me_anonymous_without_cookie(self, backend):
        r = requests.get(f"{backend}/api/auth/me", timeout=10)
        assert r.status_code == 200
        assert r.json().get("anonymous") is True


@pytest.mark.e2e
class TestCsrfEnforcement:
    """The session cookie alone must not be enough to write — a matching
    X-CSRF-Token header is required on POST/PUT/PATCH/DELETE
    (helpers/auth_middleware.py:109-113)."""

    def test_write_without_csrf_header_is_rejected(self, client):
        r = client.session.post(
            f"{client.base_url}/api/global-chats",
            json={"title": "should be rejected"},
            timeout=10,
        )
        assert r.status_code == 403, (
            f"Expected 403 without X-CSRF-Token, got {r.status_code}: {r.text[:200]}"
        )

    def test_write_with_wrong_csrf_header_is_rejected(self, client):
        r = client.session.post(
            f"{client.base_url}/api/global-chats",
            json={"title": "should be rejected"},
            headers={"X-CSRF-Token": "not-the-real-token"},
            timeout=10,
        )
        assert r.status_code == 403


@pytest.mark.e2e
class TestLogout:
    def test_logout_revokes_session(self, backend):
        """Dedicated (non-shared) session: log out, then confirm a
        subsequent authenticated call is rejected."""
        fresh = AuthedClient(backend)
        r = fresh.get("/api/global-chats")
        assert r.status_code == 200, "sanity: fresh session should be authenticated"

        fresh.logout()

        r = fresh.get("/api/global-chats")
        assert r.status_code == 401, (
            f"Expected 401 after logout, got {r.status_code}: {r.text[:200]}"
        )


@pytest.mark.e2e
class TestUnauthenticatedAccess:
    def test_protected_get_without_session_is_401(self, backend):
        r = requests.get(f"{backend}/api/global-chats", timeout=10)
        assert r.status_code == 401
