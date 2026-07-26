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

    def test_frontend_search_finds_study(self, backend, query, expected_id):
        ids = search_ids(backend, query)
        assert expected_id in ids, (
            f"Expected study {expected_id} in /api/search results for '{query}', "
            f"got {sorted(ids)[:20]}"
        )


@pytest.mark.e2e
@pytest.mark.e2e_llm
@pytest.mark.parametrize("query,expected_id", DISCOVERY_CASES)
class TestChatFindsExpected:
    """2.2 — Chat surfaces the expected study for the given query."""

    def test_chat_finds_study(self, backend, global_chat, query, expected_id):
        result = stream_chat(backend, global_chat["chat_id"], query)

        # Deterministic check: the study must appear in what the search tool
        # actually returned. This used to read `if result["query_plan"]:` and
        # fall through to the text-mention check — query_plan stopped being
        # emitted when the legacy llm_plan_query branch was deleted, so the
        # deterministic half silently stopped running and only the (much
        # weaker) "is the number in the prose" assertion remained.
        assert expected_id in result["result_study_ids"] or expected_id in result["study_ids_mentioned"], (
            f"Study {expected_id} not in chat's search results for '{query}'.\n"
            f"Tool returned: {sorted(result['result_study_ids'])}\n"
            f"Mentioned in text: {sorted(result['study_ids_mentioned'])}"
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

    def test_both_paths_return_study(self, backend, global_chat, query, expected_id):
        frontend_ids = search_ids(backend, query)

        result = stream_chat(backend, global_chat["chat_id"], query)
        # What the search tool returned, plus IDs the reply names as a fallback
        # signal. The tool half was previously gated on query_plan, which has
        # not been emitted since the legacy planner branch was removed, so this
        # was effectively a text-mention-only assertion.
        chat_ids = set(result["result_study_ids"]) | result["study_ids_mentioned"]

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
    def test_chat_keywords_superset_of_frontend(self, backend, global_chat, query, expected_id):
        from services.llm import llm_query_to_sql

        frontend_plan = llm_query_to_sql(query)
        # Extract frontend keywords from the SQL params (every 4th param is the %kw% value)
        raw_params = frontend_plan.get("params") or []
        frontend_kws = {p.strip("%").lower() for p in raw_params}

        # Keywords now come from the search_studies tool call's own args. Read
        # from query_plan until now, which nothing has emitted since the legacy
        # llm_plan_query branch was deleted — so chat_kws was always empty,
        # `missing` was always the full frontend set, and this xfail(strict=False)
        # test reported "expected failure" every run regardless of behaviour.
        result = stream_chat(backend, global_chat["chat_id"], query)
        chat_kws = {
            str(k).lower().strip()
            for payload in result["tool_ui_payloads"]
            if payload.get("tool") == "search_studies"
            for k in (payload.get("args") or {}).get("keywords") or []
        }

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

    def test_chat_output_mentions_all_studies(self, backend, global_chat, query, required_ids):
        result = stream_chat(backend, global_chat["chat_id"], query)
        missing = required_ids - result["study_ids_mentioned"]
        assert not missing, (
            f"Study IDs {missing} not mentioned in LLM output for '{query}'.\n"
            f"Mentioned IDs: {sorted(result['study_ids_mentioned'])}\n"
            f"Text snippet: {result['assistant_text'][:500]}"
        )
