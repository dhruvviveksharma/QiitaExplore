"""Fixtures for e2e parity tests.

Requires a running barnacle backend: bash ezredbiom/start_barnacle.sh
Set BARNACLE_URL env var to override the default http://localhost:5002.

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

BASE = os.environ.get("BARNACLE_URL", "http://localhost:5002")


# Override the autouse fresh_db fixture from the parent conftest — e2e tests
# never touch the local SQLite store, so the module-reload dance is a waste.
@pytest.fixture(autouse=True)
def fresh_db():
    return None


@pytest.fixture(scope="session")
def backend():
    try:
        r = requests.get(f"{BASE}/api/systems", timeout=5)
    except requests.exceptions.RequestException:
        pytest.skip("barnacle backend not running — start with: bash ezredbiom/start_barnacle.sh")
    if r.status_code != 200:
        pytest.skip(f"barnacle backend unhealthy: {r.status_code}")
    return BASE


@pytest.fixture
def global_chat(backend):
    r = requests.post(
        f"{backend}/api/global-chats",
        json={"user_id": "parity_test", "title": "parity"},
        timeout=10,
    )
    r.raise_for_status()
    chat = r.json()
    yield chat
    try:
        requests.delete(
            f"{backend}/api/global-chats/{chat['chat_id']}",
            params={"user_id": "parity_test"},
            timeout=5,
        )
    except Exception:
        pass
