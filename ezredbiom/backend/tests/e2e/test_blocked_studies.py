"""Tests that non-public studies are unreachable through every channel.

Extend BLOCKED_STUDY_IDS as you validate more non-public study IDs.
Tests 1.1–1.3 are parametrized over the list; 1.4–1.5 test chat specifically.
"""
import pytest
import requests

from parity_helpers import search_ids, stream_chat, chat_search_ids, llm_judge

# ---- Extend this list as you validate more non-public studies ----
BLOCKED_STUDY_IDS = [16084]


@pytest.mark.e2e
@pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
class TestDetailEndpointBlocked:
    """1.1 — /api/studies/<id>/detail returns 404 for non-public studies."""

    def test_detail_endpoint_returns_404(self, backend, study_id):
        r = requests.get(f"{backend}/api/studies/{study_id}/detail", timeout=10)
        assert r.status_code == 404, (
            f"Expected 404 for non-public study {study_id}, got {r.status_code}"
        )
        body = r.json()
        text = (body.get("error") or "").lower()
        assert "not" in text or "public" in text or "found" in text, (
            f"404 body doesn't mention blocked/public: {body}"
        )


@pytest.mark.e2e
@pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
class TestSearchNeverReturnsBlocked:
    """1.2 — /api/search never surfaces non-public studies regardless of query."""

    def test_search_by_study_id_string(self, backend, study_id):
        ids = search_ids(backend, str(study_id))
        assert study_id not in ids, (
            f"Study {study_id} appeared in search results for query '{study_id}'"
        )

    def test_search_by_study_id_phrase(self, backend, study_id):
        ids = search_ids(backend, f"study {study_id}")
        assert study_id not in ids, (
            f"Study {study_id} appeared in search results for query 'study {study_id}'"
        )

    def test_broad_search_excludes_blocked(self, backend, study_id):
        # Broad query returns many studies — blocked one must not slip through.
        ids = search_ids(backend, "microbiome human gut bacteria")
        assert study_id not in ids, (
            f"Study {study_id} appeared in broad search results"
        )


@pytest.mark.e2e
@pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
class TestFirstStudiesNeverReturnsBlocked:
    """1.3 — /api/studies/first never surfaces non-public studies."""

    def test_first_studies_excludes_blocked(self, backend, study_id):
        r = requests.get(f"{backend}/api/studies/first", params={"limit": 100}, timeout=15)
        r.raise_for_status()
        ids = {int(row["study_id"]) for row in r.json().get("results", [])}
        assert study_id not in ids, (
            f"Study {study_id} appeared in /api/studies/first results"
        )


@pytest.mark.e2e
@pytest.mark.e2e_llm
@pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
class TestChatDoesNotSurfaceBlocked:
    """1.4 — Chat does not return data for non-public studies when asked directly."""

    def test_chat_ask_about_blocked_study(self, backend, global_chat, study_id):
        question = f"Tell me about study {study_id}. What samples does it have?"
        result = stream_chat(backend, global_chat["chat_id"], question)

        # Deterministic check: the chat's SQL search step must not return the blocked study.
        if result["query_plan"]:
            chat_ids = chat_search_ids(backend, result["query_plan"])
            assert study_id not in chat_ids, (
                f"Study {study_id} appeared in chat's own search step results"
            )

        # LLM judge: assistant must acknowledge the study is unavailable.
        text = result["assistant_text"]
        assert llm_judge(
            question, text,
            f"indicate that study {study_id} is unavailable, private, or not found",
        ), (
            f"Judge says assistant did NOT refuse study {study_id}.\nText: {text[:500]}"
        )
        assert not llm_judge(
            question, text,
            f"provide specific metadata, sample counts, or data about study {study_id}",
        ), (
            f"Judge says assistant DID provide data for non-public study {study_id}.\nText: {text[:500]}"
        )


@pytest.mark.e2e
@pytest.mark.e2e_llm
@pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
class TestChatSearchResultsExcludeBlocked:
    """1.5 — The chat's SQL search step itself excludes non-public studies."""

    def test_chat_broad_query_excludes_blocked(self, backend, global_chat, study_id):
        result = stream_chat(
            backend,
            global_chat["chat_id"],
            "List studies available in the database",
        )
        if result["query_plan"]:
            chat_ids = chat_search_ids(backend, result["query_plan"])
            assert study_id not in chat_ids, (
                f"Study {study_id} appeared in chat's SQL search step for a broad query"
            )
