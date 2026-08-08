"""Fixtures for e2e tests against a live barnacle backend.

Requires a running barnacle backend: bash qiita_explore/start_barnacle.sh
Set BARNACLE_URL env var to override the default http://localhost:5001.

Every non-auth endpoint is default-deny (helpers/auth_middleware.py), so this
suite authenticates once per session via POST /api/auth/connect using a real
Qiita PAT (QIITA_TEST_PAT) and reuses the resulting qe_sid cookie + CSRF token
for every request. See auth_client.py for the client, parity_helpers.py for
SSE-stream and search helpers.
"""
import os
import sys
from pathlib import Path

import pytest
import requests

# Add backend to path so structural tests can import service modules directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
# Add this directory so test files can do `from parity_helpers import ...`
sys.path.insert(0, str(Path(__file__).parent))

from auth_client import AuthedClient, AuthError

BASE = os.environ.get("BARNACLE_URL", "http://localhost:5001")
RUN_PREFIX = "E2E-" + os.environ.get("E2E_RUN_ID", "manual")


# Override the autouse fresh_db fixture from the parent conftest — e2e tests
# never touch the local SQLite store, so the module-reload dance is a waste.
@pytest.fixture(autouse=True)
def fresh_db():
    return None


@pytest.fixture(scope="session")
def backend():
    try:
        requests.get(f"{BASE}/api/auth/me", timeout=5)
    except requests.exceptions.RequestException:
        pytest.skip(
            f"barnacle backend not reachable at {BASE} — "
            "start with: bash qiita_explore/start_barnacle.sh"
        )
    return BASE


@pytest.fixture(scope="session")
def client(backend):
    """Authenticated session shared by the whole run — one login, reused
    everywhere (the qe_sid session self-maintains via periodic reverify for
    up to 24h, comfortably covering an overnight run)."""
    try:
        return AuthedClient(backend)
    except AuthError as exc:
        pytest.skip(f"could not authenticate against {backend}: {exc}")


@pytest.fixture(scope="session")
def run_prefix():
    """Unique label stamped on every chat/project this run creates, so
    fixture teardown and the suite's final sweep can find (and the operator
    can visually recognize) exactly what this run touched."""
    return RUN_PREFIX


@pytest.fixture(scope="session")
def public_study_ids(client):
    """Public study IDs with samples, discovered live with a static
    fallback so the suite tolerates database drift. Sized for the pin-cap
    test (10 studies/chat, store/cache.py) as well as single-study use."""
    fallback = [11043, 10317, 10395, 10768, 16057, 2136, 1189]
    try:
        r = client.get("/api/studies/first", params={"limit": 50})
        r.raise_for_status()
        discovered = [
            s["study_id"] for s in r.json().get("results", [])
            if (s.get("num_samples") or 0) > 0
        ]
        return discovered[:15] if len(discovered) >= 3 else fallback
    except Exception:
        return fallback


@pytest.fixture
def global_chat(client, run_prefix):
    r = client.post("/api/global-chats", json={"title": f"{run_prefix} global chat"})
    r.raise_for_status()
    chat = r.json()
    yield chat
    try:
        client.delete(f"/api/global-chats/{chat['chat_id']}")
    except Exception:
        pass


@pytest.fixture
def project(client, run_prefix):
    r = client.post("/api/projects", json={"name": f"{run_prefix} project"})
    r.raise_for_status()
    proj = r.json()
    yield proj
    try:
        # project_chats / project_studies cascade-delete with the project
        # (ON DELETE CASCADE, store/db.py).
        client.delete(f"/api/projects/{proj['project_id']}")
    except Exception:
        pass


@pytest.fixture
def project_chat(client, project, run_prefix):
    r = client.post(
        f"/api/projects/{project['project_id']}/chats",
        json={"title": f"{run_prefix} project chat"},
    )
    r.raise_for_status()
    return r.json()
