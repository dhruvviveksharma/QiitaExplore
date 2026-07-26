"""Fixtures for e2e parity tests.

Requires a running barnacle backend: bash qiita_explore/start_barnacle.sh
Set BARNACLE_URL env var to override the default http://localhost:5001.

Helper functions (search_ids, stream_chat, etc.) live in helpers.py.
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

BASE = os.environ.get(
    "BARNACLE_URL",
    f"http://localhost:{os.environ.get('QIITA_EXPLORE_PORT', '5001')}",
)


# Override the autouse fresh_db fixture from the parent conftest — e2e tests
# never touch the local SQLite store, so the module-reload dance is a waste.
@pytest.fixture(autouse=True)
def fresh_db():
    return None


@pytest.fixture(scope="session")
def backend():
    """Health-check an endpoint that does NOT require a session.

    This used to GET /api/global-chats with no cookie, take the resulting 401 as
    "backend unhealthy", and skip. Every test in this directory therefore
    skipped, the suite still exited 0, and the tier looked green while running
    no assertions at all (TKT-046) — including the private-study containment
    cases, which are the ones whose silent pass is actually dangerous.
    /api/auth/login-url is in auth_middleware.PUBLIC_ENDPOINTS, so a 200 here
    means the app is genuinely serving."""
    try:
        r = requests.get(f"{BASE}/api/auth/login-url", timeout=5)
    except requests.exceptions.RequestException:
        pytest.skip(
            f"backend not reachable at {BASE} — start it with: "
            "bash qiita_explore/start_barnacle.sh  (set BARNACLE_URL or "
            "QIITA_EXPLORE_PORT if it listens elsewhere)"
        )
    if r.status_code != 200:
        pytest.skip(f"backend unhealthy at {BASE}: /api/auth/login-url returned {r.status_code}")
    return BASE


@pytest.fixture
def global_chat(backend):
    # Authenticated: routes resolve the owner from g.user_id, so the `user_id`
    # that used to ride in the body/params was ignored and the call 401'd.
    from parity_helpers import authed_session
    s = authed_session()
    r = s.post(f"{backend}/api/global-chats", json={"title": "parity"}, timeout=10)
    r.raise_for_status()
    chat = r.json()
    yield chat
    try:
        s.delete(f"{backend}/api/global-chats/{chat['chat_id']}", timeout=5)
    except Exception:
        pass
