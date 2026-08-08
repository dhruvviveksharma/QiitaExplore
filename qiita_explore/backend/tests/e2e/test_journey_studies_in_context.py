"""E2E tests verifying that studies actually reach the LLM's context.

There is no debug/prompt-dump endpoint (see docs/appendix-c-agent-tools-and-
sse.md) so these assert the closest observable proxies: step_done "<N>
studies" counts from the context builders (helpers/llm_helpers.py,
helpers/pinned_context.py), the agentic search_studies tool's result set,
and — for the deepest check — an LLM-judge confirming the assistant's answer
actually drew on that context rather than just acknowledging it.

Tests here that only assert on deterministic backend signals (step names,
counts, pinned lists) are marked `e2e` even though a real LLM call happens
as a side effect of the message stream — consistent with the rest of this
suite (e.g. TestPinSSEFlow in the pre-auth version of this file). Tests
that judge the *content* of the LLM's reply are marked `e2e_llm`.
"""
import pytest

from parity_helpers import llm_judge, stream_chat

PIN_STUDY_ID = 11043  # known public study with samples


@pytest.mark.e2e
class TestPinnedContextDeterministic:
    """Structured signals that a pinned study entered the prompt."""

    def test_pin_triggers_pinned_reports_step(self, client, global_chat):
        base = f"/api/global-chats/{global_chat['chat_id']}"
        r = client.post(f"{base}/pinned/{PIN_STUDY_ID}")
        assert r.status_code == 200

        result = stream_chat(client, global_chat["chat_id"], "Summarize what you know so far.")
        assert "pinned_reports" in result["step_done_details"], (
            f"Expected a pinned_reports context-build step, got: {result['step_done_labels']}"
        )
        assert result["step_done_details"]["pinned_reports"] == "1 studies"

    def test_pin_persists_as_context_source_across_turns(self, client, global_chat):
        base = f"/api/global-chats/{global_chat['chat_id']}"
        client.post(f"{base}/pinned/{PIN_STUDY_ID}")

        stream_chat(client, global_chat["chat_id"], "First turn.")
        second = stream_chat(client, global_chat["chat_id"], "Second turn — still pinned?")
        assert "pinned_reports" in second["step_done_details"], (
            "Expected the pinned study to keep feeding context on later turns, "
            f"got: {second['step_done_labels']}"
        )

        chat = client.get(base).json()
        assert PIN_STUDY_ID in (chat.get("pinned_studies") or [])


@pytest.mark.e2e
@pytest.mark.e2e_llm
class TestAgenticSearchSurfacesStudies:
    """Agentic path (tool-capable models): a search_studies tool call must
    surface the target study into the model's working set. Whether the
    model calls the tool at all is an LLM decision, hence e2e_llm."""

    def test_search_tool_result_contains_target_study(self, client, global_chat):
        result = stream_chat(
            client, global_chat["chat_id"],
            "Find studies about shotgun metagenomic sequencing of wild mice.",
        )
        assert result["error"] is None, f"Stream returned an error event: {result['error']}"
        found = result["result_study_ids"] | result["study_ids_mentioned"]
        assert PIN_STUDY_ID in found, (
            f"Expected study {PIN_STUDY_ID} to surface via search_studies or be mentioned "
            f"in the reply. Found: {sorted(found)}\nText: {result['assistant_text'][:400]}"
        )


@pytest.mark.e2e
@pytest.mark.e2e_llm
class TestContextFeedsAnswers:
    """The deepest check: the assistant's answer must actually be derived
    from the study context, not just acknowledge that studies are loaded."""

    def test_pinned_study_answers_sample_level_question(self, client, global_chat):
        base = f"/api/global-chats/{global_chat['chat_id']}"
        client.post(f"{base}/pinned/{PIN_STUDY_ID}")

        question = "How many samples are in the pinned study and what host organism do they come from?"
        result = stream_chat(client, global_chat["chat_id"], question)
        assert result["assistant_text"], "Expected a non-empty assistant response"
        assert llm_judge(
            question, result["assistant_text"],
            "provide a specific sample count or host organism drawn from the pinned study",
        ), (
            f"Judge: assistant did not appear to use pinned study context.\n"
            f"Text: {result['assistant_text'][:600]}"
        )

    def test_project_context_answers_from_loaded_studies(
        self, client, project, project_chat, public_study_ids,
    ):
        study_id = public_study_ids[0]
        client.post(
            f"/api/projects/{project['project_id']}/studies",
            json={"study": {"study_id": study_id}},
        )
        client.post(f"/api/projects/{project['project_id']}/studies/enrich-all")

        question = "What studies are currently loaded in this project, and what are they about?"
        result = stream_chat(
            client, project_chat["chat_id"], question,
            project_id=project["project_id"],
        )
        assert result["assistant_text"], "Expected a non-empty assistant response"
        assert llm_judge(
            question, result["assistant_text"],
            f"reference study {study_id} or describe what it is about, based on project context",
        ), (
            f"Judge: assistant did not appear to use the project's study context.\n"
            f"Text: {result['assistant_text'][:600]}"
        )
