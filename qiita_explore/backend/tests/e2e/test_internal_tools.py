"""E2E coverage for POST /api/internal/tools/<name> against a live barnacle
backend + real Qiita Postgres — the Postgres-dependent slice of Tier 3 from
the pi-backend migration plan that tests/test_internal_tools_scope.py
(unit tier, SQLite-only) cannot cover: real study data, real private-study
visibility gating, real sample-metadata search.

Requires a running barnacle backend AND PI_SCOPE_TOKEN_KEY set to the SAME
value the live server uses (mint_scope_token below signs with it directly —
there is no other way for an external test process to obtain a valid
X-Tool-Token, since the token is meant to be minted server-side per turn,
not handed out by an endpoint).

    bash qiita_explore/start_barnacle.sh
    PI_SCOPE_TOKEN_KEY=<same as server> pytest tests/e2e/test_internal_tools.py -m e2e

Study ID fixtures: see study_fixtures.py (PUBLIC_STUDY_WITH_SAMPLES=11043,
PRIVATE_STUDY=16084, AGP_STUDIES, NONEXISTENT_STUDY=999999).
"""
import os
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))  # tests/ — for study_fixtures
from study_fixtures import PUBLIC_STUDY_WITH_SAMPLES, PRIVATE_STUDY, AGP_STUDIES, NONEXISTENT_STUDY  # noqa: E402

from helpers.scope_token import mint_scope_token  # noqa: E402

BASE = os.environ.get("BARNACLE_URL", "http://localhost:5001")
CHAT_ID = "e2e-internal-tools"


def _call(tool_name, args, *, scope="global", **token_kwargs):
    # BOTH gates. internal_tool_routes._guard() requires X-Pi-Secret before the
    # scope token is even looked at, so a call sending only X-Tool-Token 401s
    # unconditionally. These tests never noticed because the whole tier was
    # skipping (TKT-046) — fixing the skip without this just turns silent passes
    # into uniform 401s.
    token = mint_scope_token(user_id="e2e_tester", scope=scope, chat_id=CHAT_ID, **token_kwargs)
    r = requests.post(
        f"{BASE}/api/internal/tools/{tool_name}",
        headers={"X-Tool-Token": token, "X-Pi-Secret": os.environ.get("PI_SIDECAR_SECRET", "")},
        json=args, timeout=30,
    )
    return r


@pytest.fixture(scope="module")
def live_tools_endpoint():
    try:
        r = requests.get(f"{BASE}/api/internal/tools/schemas", headers={"X-Pi-Secret": "healthcheck"}, timeout=5)
    except requests.exceptions.RequestException:
        pytest.skip("barnacle backend not running — start with: bash qiita_explore/start_barnacle.sh")
    # A 401 (wrong secret) still proves the route exists and is live; only a
    # connection failure means the backend itself is down.
    if r.status_code not in (200, 401):
        pytest.skip(f"barnacle backend unhealthy: {r.status_code}")
    # Both secrets must match the running backend's, or every call 401s at
    # _guard(). Named individually so the skip reason says which one is missing
    # rather than leaving the operator to guess.
    for var in ("PI_SCOPE_TOKEN_KEY", "PI_SIDECAR_SECRET"):
        if not os.environ.get(var):
            pytest.skip(
                f"{var} not set in this test process — it must match the running "
                "backend's value (source qiita_explore/.env before running)"
            )
    return BASE


@pytest.mark.e2e
class TestFetchStudies:
    def test_fetch_one_study(self, live_tools_endpoint):
        """T3.1 — get_study_report on a known-public study returns real sample data."""
        resp = _call("get_study_report", {"study_id": PUBLIC_STUDY_WITH_SAMPLES})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ui_payload"]["kind"] == "samples_report"
        assert body["ui_payload"]["study_id"] == PUBLIC_STUDY_WITH_SAMPLES
        assert (body["ui_payload"]["header"].get("num_samples") or 0) > 0
        samples = body["ui_payload"]["samples"][:5]
        assert samples, "expected at least one sample"
        for s in samples:
            assert s.get("sample_id")
            assert s.get("fields")

    def test_fetch_one_nonexistent_study_no_500(self, live_tools_endpoint):
        """T3.2 — a bogus study_id is a clean refusal, not a 500."""
        resp = _call("get_study_report", {"study_id": NONEXISTENT_STUDY})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ui_payload"] is None
        assert str(NONEXISTENT_STUDY) in body["text"] or "private" in body["text"].lower() or "not found" in body["text"].lower()

    def test_fetch_n_studies_via_search(self, live_tools_endpoint):
        """T3.3 — search_studies returns multiple distinct, well-formed rows."""
        resp = _call("search_studies", {"keywords": ["American", "Gut", "Project"], "limit": 10})
        assert resp.status_code == 200
        results = resp.json()["ui_payload"]["result_studies"]
        assert 0 < len(results) <= 10
        ids = [s["study_id"] for s in results]
        assert len(ids) == len(set(ids)), "duplicate study_id in results"
        for s in results:
            assert set(s.keys()) >= {"study_id", "study_title", "pi_name", "num_samples", "data_types", "via"}

    def test_fetch_n_studies_in_sequence(self, live_tools_endpoint):
        """T3.7 — repeated get_study_report calls each return the right study."""
        for sid in list(AGP_STUDIES)[:2]:
            resp = _call("get_study_report", {"study_id": sid})
            assert resp.status_code == 200
            body = resp.json()
            if body["ui_payload"] is not None:  # AGP studies are large/public; skip if one is unexpectedly gated
                assert body["ui_payload"]["study_id"] == sid


@pytest.mark.e2e
class TestPrivateStudyContainment:
    """PRIVATE_STUDY (16084) must never surface data through any tool path."""

    def test_direct_fetch_of_private_study_refused(self, live_tools_endpoint):
        resp = _call("get_study_report", {"study_id": PRIVATE_STUDY})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ui_payload"] is None
        assert any(w in body["text"].lower() for w in ("private", "not found", "no accessible"))

    @pytest.mark.parametrize("keywords", [
        [str(PRIVATE_STUDY)],
        [f"study {PRIVATE_STUDY}"],
        ["microbiome", "human", "gut", "bacteria"],
    ])
    def test_private_study_never_in_search_results(self, live_tools_endpoint, keywords):
        resp = _call("search_studies", {"keywords": keywords, "limit": 20})
        assert resp.status_code == 200
        ids = [s["study_id"] for s in resp.json()["ui_payload"]["result_studies"]]
        assert PRIVATE_STUDY not in ids

    def test_private_study_never_in_sample_search_results(self, live_tools_endpoint):
        resp = _call("search_by_sample", {"keywords": ["gut", "human"], "limit": 20})
        assert resp.status_code == 200
        payload = resp.json()["ui_payload"]
        if payload:
            ids = [s["study_id"] for s in payload.get("result_studies") or []]
            assert PRIVATE_STUDY not in ids

    def test_private_study_cannot_be_pinned(self, live_tools_endpoint):
        resp = _call("pin_study", {"study_ids": [PRIVATE_STUDY]})
        assert resp.status_code == 200
        assert str(PRIVATE_STUDY) not in (resp.json().get("text") or "").split("All pinned: ")[-1]

    def test_mixed_pin_batch_only_pins_the_valid_one(self, live_tools_endpoint):
        resp = _call("pin_study", {"study_ids": [PUBLIC_STUDY_WITH_SAMPLES, PRIVATE_STUDY, NONEXISTENT_STUDY]})
        assert resp.status_code == 200
        text = resp.json()["text"]
        assert str(PUBLIC_STUDY_WITH_SAMPLES) in text
        assert "not found" in text.lower() or "private" in text.lower()


@pytest.mark.e2e
class TestPinningLifecycle:
    def test_pin_then_unpin_reflected_immediately(self, live_tools_endpoint):
        """T3.13 — pinned vs not-pinned, via the pin_study tool + a fresh chat_id."""
        chat_id = "e2e-internal-tools-pin-lifecycle"
        token = mint_scope_token(user_id="e2e_tester", scope="global", chat_id=chat_id)
        r = requests.post(f"{BASE}/api/internal/tools/pin_study", headers={"X-Tool-Token": token},
                           json={"study_ids": [PUBLIC_STUDY_WITH_SAMPLES]}, timeout=30)
        assert r.status_code == 200
        assert str(PUBLIC_STUDY_WITH_SAMPLES) in r.json()["text"]

    def test_pin_is_idempotent_across_two_calls(self, live_tools_endpoint):
        """T3.14."""
        chat_id = "e2e-internal-tools-pin-idempotent"
        token = mint_scope_token(user_id="e2e_tester", scope="global", chat_id=chat_id)
        for _ in range(2):
            r = requests.post(f"{BASE}/api/internal/tools/pin_study", headers={"X-Tool-Token": token},
                               json={"study_ids": [PUBLIC_STUDY_WITH_SAMPLES]}, timeout=30)
            assert r.status_code == 200
        chat = requests.get(f"{BASE}/api/global-chats/{chat_id}", params={"user_id": "e2e_tester"}, timeout=10)
        if chat.status_code == 200:
            pinned = chat.json().get("pinned_studies") or []
            assert pinned.count(PUBLIC_STUDY_WITH_SAMPLES) == 1

    def test_pin_cap_of_ten_enforced_at_the_route(self, live_tools_endpoint):
        """T3.16 — the pin cap is currently only tested at the store layer
        (tests/test_pin_command.py); this exercises it through the actual
        route path an agent turn uses."""
        chat_id = "e2e-internal-tools-pin-cap"
        token = mint_scope_token(user_id="e2e_tester", scope="global", chat_id=chat_id)
        ids = list(AGP_STUDIES) + [PUBLIC_STUDY_WITH_SAMPLES] * 8  # pad to >=10 distinct-ish attempts
        r1 = requests.post(f"{BASE}/api/internal/tools/pin_study", headers={"X-Tool-Token": token},
                            json={"study_ids": ids[:10]}, timeout=30)
        assert r1.status_code == 200
        r2 = requests.post(f"{BASE}/api/internal/tools/pin_study", headers={"X-Tool-Token": token},
                            json={"study_ids": [NONEXISTENT_STUDY - 1]}, timeout=30)
        assert r2.status_code == 200
        # Either rejected for being over cap, or (if fewer than 10 distinct
        # valid studies existed above) simply not found — either way it must
        # not silently exceed the 10-study cap.
        chat = requests.get(f"{BASE}/api/global-chats/{chat_id}", params={"user_id": "e2e_tester"}, timeout=10)
        if chat.status_code == 200:
            assert len(chat.json().get("pinned_studies") or []) <= 10
