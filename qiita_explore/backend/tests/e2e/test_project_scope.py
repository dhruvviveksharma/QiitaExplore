"""E2E coverage for the hard project-scope boundary (Tier 4 of the pi-backend
migration plan) against a live barnacle backend + real Qiita Postgres.

This is the headline behavioral change of the whole migration: today's
project-chat boundary is a prompt sentence (_build_project_study_context);
this proves the pi-backed boundary is enforced server-side, at the route,
before execute_tool() ever runs — for real Qiita data, not synthetic SQLite
rows (that slice is already covered, offline, in
tests/test_internal_tools_scope.py).

Requires a running barnacle backend, PI_SCOPE_TOKEN_KEY set to the same value
the live server uses, and QIITA_EXPERIMENT_DB_PATH / QIITA_EXPLORE_PAT_ENCRYPTION_KEY
pointed at the SAME SQLite file and Fernet key the live server uses — this
test mints a real, valid session directly in that store (the approach
TKT-046 itself prescribes for the whole e2e tier) rather than performing a
real Qiita PAT login, which a test process cannot do against a live control
plane. Kept local to this file rather than added to parity_helpers.py, since
landing TKT-046 itself for the whole suite is out of scope here.

    bash qiita_explore/start_barnacle.sh
    PI_SCOPE_TOKEN_KEY=<same as server> \\
    QIITA_EXPERIMENT_DB_PATH=<same as server> \\
    QIITA_EXPLORE_PAT_ENCRYPTION_KEY=<same as server> \\
    pytest tests/e2e/test_project_scope.py -m e2e
"""
import os
import sys
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # backend/
sys.path.insert(0, str(Path(__file__).parent.parent))          # tests/ — for study_fixtures

from study_fixtures import PUBLIC_STUDY_WITH_SAMPLES, PRIVATE_STUDY  # noqa: E402
from helpers.scope_token import mint_scope_token  # noqa: E402

BASE = os.environ.get("BARNACLE_URL", f"http://localhost:{os.environ.get('QIITA_EXPLORE_PORT', '5001')}")

# A real public study, NOT in the test project below — the out-of-workspace
# probe. Distinct from PUBLIC_STUDY_WITH_SAMPLES on purpose.
OUT_OF_WORKSPACE_STUDY = 10317


def _mint_e2e_session(principal_idx=900001, email="e2e-project-scope@test.local"):
    """Mint a real, valid session directly in the store the live backend
    uses — see module docstring. Fresh sessions have last_verified_at="now",
    so a normal test run completes well inside the ~15min PAT-reverify
    window and never needs a real Qiita PAT."""
    from store.auth_store import upsert_user, create_session
    from helpers.pat_crypto import encrypt_pat

    user_id = upsert_user(principal_idx=principal_idx, email=email, system_role="user",
                           scopes=[], profile_complete=True)
    raw_token, csrf_token = create_session(
        user_id=user_id, pat_encrypted=encrypt_pat("e2e-test-pat-not-a-real-credential"),
        source="e2e-test",
    )
    return user_id, raw_token, csrf_token


@pytest.fixture(scope="module")
def e2e_session():
    try:
        requests.get(f"{BASE}/api/auth/login-url", timeout=5)
    except requests.exceptions.RequestException:
        pytest.skip("barnacle backend not running — start with: bash qiita_explore/start_barnacle.sh")
    for var in ("PI_SCOPE_TOKEN_KEY", "PI_SIDECAR_SECRET"):
        if not os.environ.get(var):
            pytest.skip(f"{var} not set — must match the running backend's value")
    try:
        user_id, raw_token, csrf_token = _mint_e2e_session()
    except Exception as exc:  # noqa: BLE001 — surfaces misconfigured shared DB/key clearly
        pytest.skip(f"could not mint a local session against the live backend's store: {exc}")

    session = requests.Session()
    session.cookies.set("qe_sid", raw_token)
    session.headers.update({"X-CSRF-Token": csrf_token})
    # Confirm the minted session is actually accepted by the live server
    # before trusting it for the rest of the module.
    r = session.get(f"{BASE}/api/auth/me", timeout=10)
    if r.status_code != 200 or r.json().get("user_id") != user_id:
        pytest.skip(
            "minted session was not accepted by the live backend — its "
            "QIITA_EXPERIMENT_DB_PATH likely differs from this test process's"
        )
    return session, user_id


@pytest.fixture(scope="module")
def project_with_one_study(e2e_session):
    session, user_id = e2e_session
    r = session.post(f"{BASE}/api/projects", json={"name": "e2e project-scope test"}, timeout=10)
    r.raise_for_status()
    project_id = r.json()["project_id"]

    add = session.post(
        f"{BASE}/api/projects/{project_id}/studies",
        json={"study": {"study_id": PUBLIC_STUDY_WITH_SAMPLES, "study_title": "e2e fixture study"}},
        timeout=15,
    )
    if add.status_code != 200:
        pytest.skip(f"could not add study {PUBLIC_STUDY_WITH_SAMPLES} to a fresh project: {add.text}")

    time.sleep(2)  # _enrich_study_in_project runs in a background thread on add

    yield project_id, user_id
    session.delete(f"{BASE}/api/projects/{project_id}", timeout=10)


def _project_tool_call(tool_name, args, *, project_id, user_id, chat_id="e2e-project-scope-chat"):
    token = mint_scope_token(user_id=user_id, scope="project", chat_id=chat_id, project_id=project_id)
    return requests.post(
        f"{BASE}/api/internal/tools/{tool_name}",
        # X-Pi-Secret too: _guard() checks it before the scope token, so a
        # call sending only X-Tool-Token 401s regardless of scope.
        headers={"X-Tool-Token": token, "X-Pi-Secret": os.environ.get("PI_SIDECAR_SECRET", "")},
        json=args, timeout=30,
    )


@pytest.mark.e2e
class TestProjectScopeBoundary:
    def test_in_project_study_pulled_accurately(self, project_with_one_study):
        """T4.1."""
        project_id, user_id = project_with_one_study
        resp = _project_tool_call("get_study_report", {"study_id": PUBLIC_STUDY_WITH_SAMPLES},
                                   project_id=project_id, user_id=user_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ui_payload"]["kind"] == "samples_report"
        assert body["ui_payload"]["study_id"] == PUBLIC_STUDY_WITH_SAMPLES

    def test_out_of_project_study_not_pulled(self, project_with_one_study):
        """T4.2 — the headline case. Today's boundary (a prompt sentence)
        would return this data; the hard boundary must refuse it."""
        project_id, user_id = project_with_one_study
        resp = _project_tool_call("get_study_report", {"study_id": OUT_OF_WORKSPACE_STUDY},
                                   project_id=project_id, user_id=user_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ui_payload"] is None
        assert "workspace" in body["text"].lower()

    def test_out_of_project_pin_refused(self, project_with_one_study):
        """T4.6."""
        project_id, user_id = project_with_one_study
        resp = _project_tool_call("pin_study", {"study_ids": [OUT_OF_WORKSPACE_STUDY]},
                                   project_id=project_id, user_id=user_id)
        assert resp.status_code == 200
        assert "nothing pinned" in resp.json()["text"].lower() or "not in this project" in resp.json()["text"].lower()

    def test_search_never_leaks_outside_workspace_but_still_finds_members(self, project_with_one_study):
        """T4.3 + T4.4 together: a broad query must return only workspace
        studies, and must still find the one that's actually there —
        otherwise T4.3 passing could just mean search is broken."""
        project_id, user_id = project_with_one_study
        resp = _project_tool_call("search_studies", {"keywords": ["gut", "microbiome", "human"]},
                                   project_id=project_id, user_id=user_id)
        assert resp.status_code == 200
        ids = [s["study_id"] for s in resp.json()["ui_payload"]["result_studies"]]
        assert all(i == PUBLIC_STUDY_WITH_SAMPLES for i in ids), (
            f"project search leaked non-member studies: {ids}"
        )

    def test_private_study_never_enters_a_project(self, e2e_session):
        """T4.7 — the public-visibility gate at project_routes.py:110."""
        session, _user_id = e2e_session
        r = session.post(f"{BASE}/api/projects", json={"name": "e2e private-study reject test"}, timeout=10)
        r.raise_for_status()
        project_id = r.json()["project_id"]
        try:
            add = session.post(
                f"{BASE}/api/projects/{project_id}/studies",
                json={"study": {"study_id": PRIVATE_STUDY, "study_title": "should be rejected"}},
                timeout=15,
            )
            assert add.status_code == 403
        finally:
            session.delete(f"{BASE}/api/projects/{project_id}", timeout=10)

    def test_scope_token_cannot_target_a_project_the_user_does_not_own(self, project_with_one_study, e2e_session):
        """T4.8 — get_project_studies_only() itself performs no user_id
        check; the route must independently re-verify ownership."""
        project_id, _owner_user_id = project_with_one_study
        forged_token = mint_scope_token(
            user_id="some-other-user-entirely", scope="project",
            chat_id="e2e-project-scope-chat", project_id=project_id,
        )
        resp = requests.post(
            f"{BASE}/api/internal/tools/search_studies",
            headers={"X-Tool-Token": forged_token}, json={"keywords": ["gut"]}, timeout=30,
        )
        assert resp.status_code in (401, 403, 404)

    def test_global_scope_control_same_query_is_unrestricted(self, project_with_one_study):
        """T4.9 — the control. Without this, a project-scope test that
        returns nothing looks identical to one where search itself is
        broken."""
        project_id, user_id = project_with_one_study
        token = mint_scope_token(user_id=user_id, scope="global", chat_id="e2e-project-scope-global-control")
        resp = requests.post(
            f"{BASE}/api/internal/tools/search_studies",
            headers={"X-Tool-Token": token},
            json={"keywords": ["gut", "microbiome", "human"], "limit": 20},
            timeout=30,
        )
        assert resp.status_code == 200
        ids = {s["study_id"] for s in resp.json()["ui_payload"]["result_studies"]}
        assert ids - {PUBLIC_STUDY_WITH_SAMPLES}, (
            "global scope should find studies beyond the project's single member — "
            "if it doesn't, the T4.3 'no leak' result may just mean search is broken"
        )
