"""Structural parity tests — HTTP, no LLM.

Verify that both the chat planner path and the frontend planner path produce
search results that share the same visibility contract. All assertions go
through /api/search or /api/studies/<id>/detail via the authenticated client
(every endpoint requires a session — helpers/auth_middleware.py).

Requires: running barnacle backend (bash qiita_explore/start_barnacle.sh)
and QIITA_TEST_PAT set to a valid Qiita personal access token.
"""
import pytest

from parity_helpers import search_ids

BLOCKED_STUDY_IDS = [16084]

# Each entry: (keyword_list_simulating_chat_planner_output, expected_study_id)
CHAT_PLANNER_CASES = [
    (["wild", "mice", "shotgun", "metagenomic"], 11043),
    (["wild", "mice", "metagenomics", "WGS", "feral"], 11043),
]


@pytest.mark.e2e
@pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
class TestDetailEndpointEnforcesVisibility:
    """3.1 — /api/studies/<id>/detail proves non-public studies are blocked at the HTTP layer."""

    def test_detail_returns_404_for_non_public(self, client, study_id):
        r = client.get(f"/api/studies/{study_id}/detail")
        assert r.status_code == 404, (
            f"Expected 404 for non-public study {study_id}, got {r.status_code}"
        )


@pytest.mark.e2e
class TestDetailEndpointAllowsPublic:
    """3.2 — /api/studies/11043/detail confirms the public study is accessible."""

    def test_detail_returns_200_for_public(self, client):
        r = client.get("/api/studies/11043/detail")
        assert r.status_code == 200, (
            f"Expected study 11043 to be public and accessible, got {r.status_code}"
        )
        data = r.json()
        assert data.get("study_id") == 11043


@pytest.mark.e2e
class TestChatPlannerPathRespectsVisibility:
    """3.3 — Chat planner keyword searches find expected studies and exclude non-public ones.

    Simulates the agent keyword path by passing keyword strings to /api/search.
    """

    @pytest.mark.parametrize("keywords,expected_id", CHAT_PLANNER_CASES)
    def test_chat_keywords_find_expected(self, client, keywords, expected_id):
        query = " ".join(keywords)
        ids = search_ids(client, query)
        assert expected_id in ids, (
            f"Expected {expected_id} in /api/search for chat-planner keywords '{query}'. "
            f"Got IDs (sample): {sorted(ids)[:20]}"
        )

    @pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
    def test_chat_keywords_exclude_blocked(self, client, study_id):
        ids = search_ids(client, "microbiome gut bacteria human host")
        assert study_id not in ids, (
            f"Non-public study {study_id} appeared in chat-planner keyword search"
        )


@pytest.mark.e2e
class TestFrontendPlannerPathRespectsVisibility:
    """3.4 — Frontend planner (llm_query_to_sql) searches find expected studies and exclude non-public ones.

    Passes natural-language queries directly to /api/search, which is the production path.
    """

    def test_frontend_planner_finds_11043(self, client):
        ids = search_ids(client, "shotgun metagenomic wild mice")
        assert 11043 in ids, (
            f"Frontend planner did not surface study 11043. "
            f"Got IDs (sample): {sorted(ids)[:20]}"
        )

    @pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
    def test_frontend_planner_excludes_blocked(self, client, study_id):
        ids = search_ids(client, "microbiome gut bacteria human")
        assert study_id not in ids, (
            f"Non-public study {study_id} appeared in frontend planner search results"
        )
