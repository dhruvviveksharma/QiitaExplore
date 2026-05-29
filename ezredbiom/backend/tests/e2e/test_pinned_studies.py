"""Tests for study pinning via chat and sample querying on pinned studies.

Pinning happens exclusively through the stream endpoint by passing report_study_id.
After pinning, the study appears in chat["pinned_studies"] and its sample data feeds
into the LLM context for follow-up queries.
"""
import pytest
import requests

from parity_helpers import stream_chat, llm_judge

# Single public study we know has samples
PIN_STUDY_ID = 11043
BLOCKED_STUDY_ID = 16084


@pytest.fixture(scope="module")
def pinnable_studies(backend):
    """Return the first 3 public study IDs that have at least 1 sample.

    Pulled dynamically from /api/studies/first so the test is not brittle
    against specific IDs beyond the one we've validated (11043).
    """
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


def _get_chat(backend, chat_id):
    """Fetch the current state of a global chat."""
    r = requests.get(
        f"{backend}/api/global-chats/{chat_id}",
        params={"user_id": "parity_test"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# 4.1–4.3 — Pinning a single study
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestPinStudyViaSampleReport:
    """Pin a public study via report_study_id, verify payload and chat state."""

    def test_pin_loads_valid_sample_payload(self, backend, global_chat):
        """4.1 — ui event carries a well-formed samples_report payload."""
        result = stream_chat(
            backend, global_chat["chat_id"],
            f"Load the sample data for study {PIN_STUDY_ID}",
            report_study_id=PIN_STUDY_ID,
        )
        payload = result["ui_payload"]
        assert payload is not None, (
            "Expected a 'ui' SSE event with samples_report payload, got None"
        )
        assert payload.get("kind") == "samples_report", (
            f"Expected kind=samples_report, got: {payload.get('kind')}"
        )
        assert payload.get("study_id") == PIN_STUDY_ID

        header = payload.get("header") or {}
        assert (header.get("num_samples") or 0) > 0, (
            f"Expected num_samples > 0 in header, got: {header}"
        )

        samples = payload.get("samples") or []
        assert len(samples) > 0, "Expected at least one sample in payload"
        for s in samples[:5]:  # spot-check first 5
            assert s.get("sample_id"), f"Sample missing sample_id: {s}"
            assert s.get("fields"), f"Sample {s.get('sample_id')} has empty fields"

    def test_study_appears_in_pinned_list_after_pin(self, backend, global_chat):
        """4.2 — After pinning, the study ID appears in chat.pinned_studies."""
        stream_chat(
            backend, global_chat["chat_id"],
            f"Load samples for study {PIN_STUDY_ID}",
            report_study_id=PIN_STUDY_ID,
        )
        chat = _get_chat(backend, global_chat["chat_id"])
        assert PIN_STUDY_ID in (chat.get("pinned_studies") or []), (
            f"Study {PIN_STUDY_ID} not in pinned_studies after pinning. "
            f"Got: {chat.get('pinned_studies')}"
        )

    def test_blocked_study_not_pinned(self, backend, global_chat):
        """4.3 — Sending report_study_id for a non-public study does NOT pin it."""
        result = stream_chat(
            backend, global_chat["chat_id"],
            f"Load sample data for study {BLOCKED_STUDY_ID}",
            report_study_id=BLOCKED_STUDY_ID,
        )
        # No ui event should have been emitted.
        assert result["ui_payload"] is None, (
            f"Expected no ui payload for blocked study {BLOCKED_STUDY_ID}, "
            f"got: {result['ui_payload']}"
        )
        # step_done labels should indicate the study is private.
        labels = " ".join(label for _, label in result["step_done_labels"]).lower()
        assert any(w in labels for w in ("private", "not", "unavailable", "no accessible")), (
            f"Expected a refusal label in step_done events, got labels: {result['step_done_labels']}"
        )
        # Study must not be in pinned_studies.
        chat = _get_chat(backend, global_chat["chat_id"])
        assert BLOCKED_STUDY_ID not in (chat.get("pinned_studies") or []), (
            f"Blocked study {BLOCKED_STUDY_ID} was pinned despite being non-public"
        )


# ---------------------------------------------------------------------------
# 4.4 — Pin 3 different studies
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestPin3Studies:
    """Pin 3 different studies to the same chat and verify all are tracked."""

    def test_pin_3_different_studies(self, backend, global_chat, pinnable_studies):
        """4.4 — Three distinct studies can be pinned to a single chat."""
        for study_id in pinnable_studies:
            stream_chat(
                backend, global_chat["chat_id"],
                f"Load sample report for study {study_id}",
                report_study_id=study_id,
            )

        chat = _get_chat(backend, global_chat["chat_id"])
        pinned = set(chat.get("pinned_studies") or [])

        assert len(pinned) >= 3, (
            f"Expected at least 3 pinned studies, got {len(pinned)}: {pinned}"
        )
        for study_id in pinnable_studies:
            assert study_id in pinned, (
                f"Study {study_id} was not pinned after sending report_study_id"
            )


# ---------------------------------------------------------------------------
# 4.5–4.6 — Query samples from pinned studies (LLM judge)
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.e2e_llm
class TestSampleQueryOnPinnedStudy:
    """After pinning, the assistant can answer questions using pinned sample context."""

    def test_query_pinned_study_samples(self, backend, global_chat):
        """4.5 — Follow-up question on a pinned study gets a sample-informed answer."""
        # Pin the study first.
        stream_chat(
            backend, global_chat["chat_id"],
            f"Load sample data for study {PIN_STUDY_ID}",
            report_study_id=PIN_STUDY_ID,
        )
        question = (
            "How many samples are in this study and what host organism "
            "do they come from?"
        )
        result = stream_chat(backend, global_chat["chat_id"], question)
        assert result["assistant_text"], "Expected a non-empty assistant response"
        assert llm_judge(
            question, result["assistant_text"],
            "provide specific sample count or host organism information from the pinned study",
        ), (
            f"Judge says assistant did not answer the sample question.\n"
            f"Text: {result['assistant_text'][:600]}"
        )

    def test_query_samples_across_3_pinned_studies(
        self, backend, global_chat, pinnable_studies
    ):
        """4.6 — After pinning 3 studies, the assistant can compare sample counts."""
        for study_id in pinnable_studies:
            stream_chat(
                backend, global_chat["chat_id"],
                f"Load sample report for study {study_id}",
                report_study_id=study_id,
            )

        question = "Compare the number of samples across the pinned studies"
        result = stream_chat(backend, global_chat["chat_id"], question)
        assert result["assistant_text"], "Expected a non-empty assistant response"
        assert llm_judge(
            question, result["assistant_text"],
            "compare or contrast sample counts or sizes across multiple studies",
        ), (
            f"Judge says assistant did not compare sample counts.\n"
            f"Text: {result['assistant_text'][:600]}"
        )
