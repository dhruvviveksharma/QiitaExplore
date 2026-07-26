"""Tests that non-public studies are unreachable through every channel.

Extend BLOCKED_STUDY_IDS as you validate more non-public study IDs.
Tests 1.1–1.3 are parametrized over the list; 1.4–1.5 test chat specifically.
"""
import pytest
import requests

from parity_helpers import search_ids, stream_chat, llm_judge

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

        # Deterministic check: no tool call may return the blocked study.
        # Guarded on `if result["query_plan"]` until now — nothing has emitted
        # that event since the legacy llm_plan_query branch was deleted, so this
        # containment check silently stopped running.
        assert study_id not in result["result_study_ids"], (
            f"Study {study_id} was returned by a tool call in the chat.\n"
            f"Tool results: {sorted(result['result_study_ids'])}"
        )
        for payload in result["tool_ui_payloads"]:
            assert payload.get("study_id") != study_id, (
                f"A tool returned a report payload for non-public study {study_id}"
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
    """1.5 — The chat's search step itself excludes non-public studies.

    Before this rewrite the whole test body sat behind `if result["query_plan"]`,
    which has been permanently falsy since the legacy llm_plan_query branch was
    deleted. The method therefore asserted NOTHING and reported green — on a
    non-public-data containment check.
    """

    def test_chat_broad_query_excludes_blocked(self, backend, global_chat, study_id):
        result = stream_chat(
            backend,
            global_chat["chat_id"],
            "List studies available in the database",
        )
        # A search that returned nothing would make the containment assertion
        # below trivially true — which is the failure mode this test just came
        # out of. Require the search to have actually produced results first.
        assert result["result_study_ids"], (
            "the broad query returned no studies at all, so this proves nothing "
            "about containment — check the model issued a search_studies call"
        )
        assert study_id not in result["result_study_ids"], (
            f"Non-public study {study_id} appeared in search results for a broad query.\n"
            f"Returned: {sorted(result['result_study_ids'])}"
        )


@pytest.mark.e2e
@pytest.mark.e2e_llm
@pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
class TestAgentToolsDoNotLeakBlocked:
    """1.6 — get_study_report and pin_study tool calls reject non-public studies."""

    def test_get_study_report_tool_refuses_blocked(self, backend, global_chat, study_id):
        question = (
            f"Give me the full sample report for study {study_id}, "
            "including title, PI, and sample counts."
        )
        result = stream_chat(backend, global_chat["chat_id"], question)

        # Deterministic: if get_study_report fired, its payload must not leak
        # a samples_report for this blocked study.
        for payload in result["tool_ui_payloads"]:
            if payload and payload.get("kind") == "samples_report":
                assert payload.get("study_id") != study_id, (
                    f"get_study_report tool returned samples_report payload for "
                    f"blocked study {study_id}: {payload}"
                )

        text = result["assistant_text"]
        assert llm_judge(
            question, text,
            f"indicate that study {study_id} is private, unavailable, or not accessible",
        ), f"Judge says assistant did NOT refuse study {study_id} report.\nText: {text[:500]}"
        assert not llm_judge(
            question, text,
            f"provide the study title, PI name, or sample count for study {study_id}",
        ), f"Judge says assistant DID leak report data for study {study_id}.\nText: {text[:500]}"

    def test_pin_study_tool_refuses_blocked(self, backend, global_chat, study_id):
        question = f"Pin study {study_id} to this chat."
        result = stream_chat(backend, global_chat["chat_id"], question)

        # Deterministic: the study must not end up in the chat's pinned list.
        assert study_id not in (result["pinned_studies"] or []), (
            f"Study {study_id} was pinned despite being non-public. "
            f"pinned_studies={result['pinned_studies']}"
        )

        text = result["assistant_text"]
        assert llm_judge(
            question, text,
            f"indicate that study {study_id} could not be pinned, is private, or is not found",
        ), f"Judge says assistant did NOT refuse to pin study {study_id}.\nText: {text[:500]}"
