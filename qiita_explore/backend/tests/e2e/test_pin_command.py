"""E2E tests for the /pin slash command (pin_study_ids path).

The /pin flow differs from /report:
  - Accepts a list of study IDs (not a single ID)
  - Emits: step_start/pin_studies → step_done/pin_studies →
           step_start/deep_context → step_done/deep_context → tokens → done
  - No `ui` SSE event (no inline browser)
  - done event carries pinned_studies: [...] (full list after operation)

Requires a running barnacle backend: bash qiita_explore/start_barnacle.sh
"""
import pytest
import requests

from parity_helpers import stream_chat, llm_judge

PIN_STUDY_ID  = 11043   # known public study with samples
INVALID_ID    = 999999  # guaranteed nonexistent


def _get_chat(backend, chat_id):
    r = requests.get(
        f"{backend}/api/global-chats/{chat_id}",
        params={"user_id": "parity_test"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


@pytest.fixture(scope="module")
def pinnable_studies(backend):
    """Return 3 public study IDs with at least 1 sample."""
    r = requests.get(f"{backend}/api/studies/first", params={"limit": 20}, timeout=15)
    r.raise_for_status()
    studies = [
        s["study_id"]
        for s in r.json().get("results", [])
        if (s.get("num_samples") or 0) > 0
    ]
    if len(studies) < 3:
        pytest.skip(f"Not enough public studies with samples (found {len(studies)}, need 3)")
    return studies[:3]


# ---------------------------------------------------------------------------
# SSE event structure
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestPinSSEFlow:
    """Verify the SSE event sequence produced by pin_study_ids."""

    def test_pin_single_study_sse_flow(self, backend, global_chat):
        """pin_studies and deep_context steps both appear; done carries pinned_studies."""
        result = stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {PIN_STUDY_ID} - Pinning studies",
            pin_study_ids=[PIN_STUDY_ID],
        )
        step_names = [name for name, _ in result["step_done_labels"]]
        assert "pin_studies" in step_names, (
            f"Expected step_done/pin_studies, got: {result['step_done_labels']}"
        )
        assert "deep_context" in step_names, (
            f"Expected step_done/deep_context after pinning, got: {result['step_done_labels']}"
        )
        assert PIN_STUDY_ID in result["pinned_studies"], (
            f"Study {PIN_STUDY_ID} not in done.pinned_studies: {result['pinned_studies']}"
        )

    def test_pin_no_ui_event(self, backend, global_chat):
        """/pin must NOT emit a ui event (that's /report's job)."""
        result = stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {PIN_STUDY_ID} - Pinning studies",
            pin_study_ids=[PIN_STUDY_ID],
        )
        assert result["ui_payload"] is None, (
            f"Expected no ui payload for /pin, got: {result['ui_payload']}"
        )

    def test_pin_produces_token_stream(self, backend, global_chat):
        """LLM acknowledgment tokens must be emitted after pinning."""
        result = stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {PIN_STUDY_ID} - Pinning studies",
            pin_study_ids=[PIN_STUDY_ID],
        )
        assert result["assistant_text"], "Expected non-empty LLM acknowledgment after /pin"


# ---------------------------------------------------------------------------
# Pinning outcomes
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestPinOutcomes:
    """Verify which studies end up pinned under various input conditions."""

    def test_pin_study_appears_in_chat_state(self, backend, global_chat):
        """After /pin, the study ID appears in the chat's pinned_studies via GET."""
        stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {PIN_STUDY_ID} - Pinning studies",
            pin_study_ids=[PIN_STUDY_ID],
        )
        chat = _get_chat(backend, global_chat["chat_id"])
        assert PIN_STUDY_ID in (chat.get("pinned_studies") or []), (
            f"Study {PIN_STUDY_ID} not in chat.pinned_studies: {chat.get('pinned_studies')}"
        )

    def test_pin_multiple_studies(self, backend, global_chat, pinnable_studies):
        """Pinning 3 valid IDs in one request — all 3 appear in chat state."""
        stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {' '.join(str(s) for s in pinnable_studies)} - Pinning studies",
            pin_study_ids=pinnable_studies,
        )
        chat = _get_chat(backend, global_chat["chat_id"])
        pinned = set(chat.get("pinned_studies") or [])
        for sid in pinnable_studies:
            assert sid in pinned, f"Study {sid} missing from pinned_studies: {pinned}"

    def test_pin_invalid_study_not_pinned(self, backend, global_chat):
        """Nonexistent study ID is reported as not found; chat has no pinned studies."""
        result = stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {INVALID_ID} - Pinning studies",
            pin_study_ids=[INVALID_ID],
        )
        detail = result["step_done_details"].get("pin_studies", "").lower()
        assert "not found" in detail, (
            f"Expected 'not found' in pin_studies detail, got: {detail!r}"
        )
        chat = _get_chat(backend, global_chat["chat_id"])
        assert INVALID_ID not in (chat.get("pinned_studies") or [])

    def test_pin_mixed_valid_invalid(self, backend, global_chat):
        """One valid + one invalid ID: valid pinned, invalid reported as not found."""
        result = stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {PIN_STUDY_ID} {INVALID_ID} - Pinning studies",
            pin_study_ids=[PIN_STUDY_ID, INVALID_ID],
        )
        detail = result["step_done_details"].get("pin_studies", "").lower()
        assert "pinned" in detail, f"Expected 'pinned' count in detail: {detail!r}"
        assert "not found" in detail, f"Expected 'not found' in detail: {detail!r}"

        chat = _get_chat(backend, global_chat["chat_id"])
        pinned = chat.get("pinned_studies") or []
        assert PIN_STUDY_ID in pinned, f"Valid study {PIN_STUDY_ID} not pinned"
        assert INVALID_ID not in pinned, f"Invalid study {INVALID_ID} should not be pinned"

    def test_pin_idempotent_via_api(self, backend, global_chat):
        """Pinning the same study via two separate requests → appears exactly once."""
        stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {PIN_STUDY_ID} - Pinning studies",
            pin_study_ids=[PIN_STUDY_ID],
        )
        stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {PIN_STUDY_ID} - Pinning studies",
            pin_study_ids=[PIN_STUDY_ID],
        )
        chat = _get_chat(backend, global_chat["chat_id"])
        pinned = chat.get("pinned_studies") or []
        assert pinned.count(PIN_STUDY_ID) == 1, (
            f"Study {PIN_STUDY_ID} appears {pinned.count(PIN_STUDY_ID)} times: {pinned}"
        )


# ---------------------------------------------------------------------------
# Pinned context persists across turns
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestPinContextPersistence:
    """Verify pinned studies feed into subsequent regular messages."""

    def test_pin_context_reloaded_on_next_message(self, backend, global_chat):
        """After /pin, a regular follow-up triggers the deep_context step for pinned data."""
        stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {PIN_STUDY_ID} - Pinning studies",
            pin_study_ids=[PIN_STUDY_ID],
        )
        follow_up = stream_chat(
            backend, global_chat["chat_id"],
            "What studies are pinned in this chat?",
        )
        step_names = [name for name, _ in follow_up["step_done_labels"]]
        assert "deep_context" in step_names, (
            f"Expected deep_context step in follow-up (pinned data reload), got: {follow_up['step_done_labels']}"
        )


# ---------------------------------------------------------------------------
# LLM-judged: context quality
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.e2e_llm
class TestPinContextQuality:
    """After /pin, the LLM can answer sample-level questions from pinned context."""

    def test_pin_context_feeds_llm(self, backend, global_chat):
        """Follow-up question about samples answered using pinned study context."""
        stream_chat(
            backend, global_chat["chat_id"],
            f"/pin {PIN_STUDY_ID} - Pinning studies",
            pin_study_ids=[PIN_STUDY_ID],
        )
        question = "How many samples are in the pinned study and what host organism do they come from?"
        result = stream_chat(backend, global_chat["chat_id"], question)
        assert result["assistant_text"], "Expected non-empty assistant response"
        assert llm_judge(
            question, result["assistant_text"],
            "provide specific sample count or host organism information from the pinned study",
        ), (
            f"Judge: assistant did not use pinned sample context.\n"
            f"Text: {result['assistant_text'][:600]}"
        )
