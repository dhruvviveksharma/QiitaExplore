"""Tests that discoverable studies surface through both /api/search and chat.

Extend DISCOVERY_CASES as you validate more (query, expected_study_id) pairs.
"""
import pytest

from parity_helpers import search_ids, stream_chat, chat_search_ids, llm_judge

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

        # Deterministic check: expected study must be in the chat's search step.
        in_search = False
        if result["query_plan"]:
            chat_ids = chat_search_ids(client, result["query_plan"])
            in_search = expected_id in chat_ids
        assert in_search or expected_id in result["study_ids_mentioned"], (
            f"Study {expected_id} not in chat's search results for '{query}'.\n"
            f"Query plan: {result['query_plan']}"
        )

        # LLM judge: assistant must recommend or mention a relevant study.
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
        chat_ids = set()
        if result["query_plan"]:
            chat_ids = chat_search_ids(client, result["query_plan"])
        # Chat IDs from text mentions are a fallback signal too
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
@pytest.mark.parametrize("query,expected_id", DISCOVERY_CASES)
class TestChatKeywordsAreRicher:
    """2.4 — Chat's LLM planner expands to a superset of the frontend's keywords.

    Marked xfail because the LLM may occasionally omit an exact input keyword.
    This test documents the relationship; failure is informational.
    """

    @pytest.mark.xfail(
        reason="LLM may not always include every exact input keyword from the frontend planner",
        strict=False,
    )
    def test_chat_keywords_superset_of_frontend(self, client, global_chat, query, expected_id):
        from services.llm import llm_query_to_sql

        frontend_plan = llm_query_to_sql(query)
        # Extract frontend keywords from the SQL params (every 4th param is the %kw% value)
        raw_params = frontend_plan.get("params") or []
        frontend_kws = {p.strip("%").lower() for p in raw_params}

        result = stream_chat(client, global_chat["chat_id"], query)
        chat_kws = {k.lower().strip() for k in (result.get("query_plan") or {}).get("keywords", [])}

        missing = frontend_kws - chat_kws
        assert not missing, (
            f"Chat planner missing frontend keywords: {missing}\n"
            f"Frontend keywords: {frontend_kws}\n"
            f"Chat keywords (sample): {sorted(chat_kws)[:20]}"
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
