"""Tests for /report: loads a samples_report payload but does NOT pin the study.

Pinning is now exclusively via /pin (pin_study_ids), covered by test_pin_command.py.
/report is a view-only operation: the ui SSE event carries the sample browser
payload; the study does not appear in chat.pinned_studies afterwards.
"""
import pytest
import requests

from parity_helpers import stream_chat

# Single public study we know has samples
PIN_STUDY_ID = 11043
BLOCKED_STUDY_ID = 16084


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
# 4.1–4.3 — /report loads samples, does NOT pin
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestReportLoadsSamplesNoPin:
    """4.1 — ui event carries a well-formed samples_report payload.
    4.2 — /report does NOT add the study to chat.pinned_studies.
    4.3 — /report for a blocked study emits no ui payload and still does not pin.
    """

    def test_report_loads_valid_sample_payload(self, backend, global_chat):
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

    def test_report_does_not_pin(self, backend, global_chat):
        """4.2 — /report must NOT add the study to chat.pinned_studies."""
        stream_chat(
            backend, global_chat["chat_id"],
            f"Load samples for study {PIN_STUDY_ID}",
            report_study_id=PIN_STUDY_ID,
        )
        chat = _get_chat(backend, global_chat["chat_id"])
        assert PIN_STUDY_ID not in (chat.get("pinned_studies") or []), (
            f"Study {PIN_STUDY_ID} was pinned by /report — it should not be. "
            f"Got: {chat.get('pinned_studies')}"
        )

    def test_report_done_event_has_no_pins(self, backend, global_chat):
        """4.2b — done SSE event for /report carries no pinned_studies list."""
        result = stream_chat(
            backend, global_chat["chat_id"],
            f"Load samples for study {PIN_STUDY_ID}",
            report_study_id=PIN_STUDY_ID,
        )
        assert result["pinned_studies"] == [], (
            f"Expected empty pinned_studies in done event for /report, "
            f"got: {result['pinned_studies']}"
        )

    def test_blocked_study_not_pinned(self, backend, global_chat):
        """4.3 — /report for a non-public study emits no ui payload and does not pin."""
        result = stream_chat(
            backend, global_chat["chat_id"],
            f"Load sample data for study {BLOCKED_STUDY_ID}",
            report_study_id=BLOCKED_STUDY_ID,
        )
        assert result["ui_payload"] is None, (
            f"Expected no ui payload for blocked study {BLOCKED_STUDY_ID}, "
            f"got: {result['ui_payload']}"
        )
        labels = " ".join(label for _, label in result["step_done_labels"]).lower()
        assert any(w in labels for w in ("private", "not", "unavailable", "no accessible")), (
            f"Expected a refusal label in step_done events, got: {result['step_done_labels']}"
        )
        chat = _get_chat(backend, global_chat["chat_id"])
        assert BLOCKED_STUDY_ID not in (chat.get("pinned_studies") or []), (
            f"Blocked study {BLOCKED_STUDY_ID} was pinned despite being non-public"
        )
