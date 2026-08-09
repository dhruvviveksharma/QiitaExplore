"""Tests that discoverable studies surface through both /api/search and chat.

Extend DISCOVERY_CASES as you validate more (query, expected_study_id) pairs.
"""
import pytest

from parity_helpers import search_ids, stream_chat, llm_judge

# ---- Extend this list as you validate more (query, expected_id) pairs ----
DISCOVERY_CASES = [
    ("shotgun metagenomic studies on wild mice", 11043),
]

# ---- Multi-study cases: ALL listed IDs must appear in LLM output ----
MULTI_STUDY_CASES = [
    (
        "Find studies related to the American Gut Project",
        {16057, 2136, 1189},
    ),
]


@pytest.mark.e2e
@pytest.mark.parametrize("query,expected_id", DISCOVERY_CASES)
class TestFrontendSearchFindsExpected:
    """2.1 — /api/search surfaces the expected study for the given query."""

    def test_frontend_search_finds_study(self, client, query, expected_id):
        ids = search_ids(client, query)
        assert expected_id in ids, (
            f"Expected study {expected_id} in /api/search results for '{query}', "
            f"got {sorted(ids)[:20]}"
        )


@pytest.mark.e2e
@pytest.mark.e2e_llm
@pytest.mark.parametrize("query,expected_id", DISCOVERY_CASES)
class TestChatFindsExpected:
    """2.2 — Chat surfaces the expected study for the given query."""

    def test_chat_finds_study(self, client, global_chat, query, expected_id):
        result = stream_chat(client, global_chat["chat_id"], query)

        in_search = expected_id in result["result_study_ids"]
        assert in_search or expected_id in result["study_ids_mentioned"], (
            f"Study {expected_id} not in chat's search results for '{query}'.\n"
            f"Tool IDs: {sorted(result['result_study_ids'])}"
        )

        assert llm_judge(
            query, result["assistant_text"],
            "mention or recommend a specific study related to the query",
        ), (
            f"Judge says assistant did not meaningfully address '{query}'.\n"
            f"Text: {result['assistant_text'][:500]}"
        )


@pytest.mark.e2e
@pytest.mark.e2e_llm
@pytest.mark.parametrize("query,expected_id", DISCOVERY_CASES)
class TestBothPathsIntersectOnExpected:
    """2.3 — Both frontend search and chat search step agree on returning the expected study."""

    def test_both_paths_return_study(self, client, global_chat, query, expected_id):
        frontend_ids = search_ids(client, query)

        result = stream_chat(client, global_chat["chat_id"], query)
        chat_ids = set(result["result_study_ids"])
        chat_ids |= result["study_ids_mentioned"]

        assert expected_id in frontend_ids, (
            f"Study {expected_id} missing from frontend /api/search for '{query}'"
        )
        assert expected_id in chat_ids, (
            f"Study {expected_id} missing from chat path for '{query}'. "
            f"Chat search IDs (sample): {sorted(chat_ids)[:20]}"
        )


@pytest.mark.e2e
@pytest.mark.e2e_llm
@pytest.mark.parametrize("query,required_ids", MULTI_STUDY_CASES)
class TestChatFindsAllExpectedStudies:
    """All required study IDs must appear in the LLM output for the query."""

    def test_chat_output_mentions_all_studies(self, client, global_chat, query, required_ids):
        result = stream_chat(client, global_chat["chat_id"], query)
        missing = required_ids - result["study_ids_mentioned"]
        assert not missing, (
            f"Study IDs {missing} not mentioned in LLM output for '{query}'.\n"
            f"Mentioned IDs: {sorted(result['study_ids_mentioned'])}\n"
            f"Text snippet: {result['assistant_text'][:500]}"
        )
