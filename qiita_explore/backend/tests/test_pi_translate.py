"""Translation and segment-parity tests for helpers/pi_translate.py.

Fed by tests/fixtures/pi_events_sample_turn.json — a REAL captured pi event
stream (via pi_sidecar's faux-provider test harness, one search_studies tool
call + a text answer), not a hand-constructed guess at the shapes. This is
the segment-parity test docs/10-testing.md has been asking for: it closes the
dual-authoring hazard where the SSE/segment shape is built independently in
Python and JS by making one Python reducer the single source of truth for
both — and pins that reducer's output to a byte-identical shape against a
real pi event stream.
"""
import json
import os
import re

import pytest

from helpers.pi_translate import translate, build_segments, _tool_step_name, TurnTranslator

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "pi_events_sample_turn.json")


@pytest.fixture
def sample_events():
    with open(FIXTURE_PATH) as f:
        return json.load(f)


class TestTranslateSSESequence:
    def test_first_event_is_agent_start(self, sample_events):
        seq = list(translate(sample_events))
        assert seq[0] == ("agent_start", {})

    def test_tool_call_and_result_are_emitted_in_order(self, sample_events):
        seq = list(translate(sample_events))
        names = [name for name, _ in seq]
        assert names.index("segment_tool_call") < names.index("segment_tool_result")

    def test_tool_call_payload_shape(self, sample_events):
        call_payload = next(p for n, p in translate(sample_events) if n == "segment_tool_call")
        assert set(call_payload.keys()) == {"name", "label", "args"}
        assert call_payload["args"] == {"keywords": ["wild", "mice"]}
        assert call_payload["name"].startswith("tool_search_studies_")

    def test_correlation_name_uses_the_full_tool_call_id(self, sample_events):
        """The join key between call and result on both the server reducer and
        app_state.js:314-322 — must be the FULL toolCallId, not a [:6] prefix.
        pi's ids are shaped "tool:<epoch-ms>:<rand>", so a truncated prefix is
        "tool:1" for every call (until the year 2286): two calls to the same
        tool in a turn would then share a correlation key and one result would
        populate both cards. See test_distinct_calls_to_the_same_tool_do_not_collide
        for the failure this guards against."""
        tool_start = next(e for e in sample_events if e["type"] == "tool_execution_start")
        expected = _tool_step_name(tool_start["toolName"], tool_start["toolCallId"])
        assert expected == f"tool_search_studies_{tool_start['toolCallId']}"
        assert tool_start["toolCallId"] in expected

        call_payload = next(p for n, p in translate(sample_events) if n == "segment_tool_call")
        result_payload = next(p for n, p in translate(sample_events) if n == "segment_tool_result")
        assert call_payload["name"] == expected
        assert result_payload["name"] == expected

    def test_distinct_calls_to_the_same_tool_do_not_collide(self):
        """Regression for the [:6]-truncation bug: pi's real toolCallIds all
        share the "tool:1..." prefix (epoch-ms since 2026 starts with 1), so
        truncating to 6 chars collapsed every same-tool call in a turn onto one
        correlation key. Two get_study_report calls in one turn must produce
        two distinct names and each result must land on its own call."""
        events = [
            {"type": "tool_execution_start", "toolCallId": "tool:1784970149356:aaaaaaaaaaa",
             "toolName": "get_study_report", "args": {"study_id": 111}},
            {"type": "tool_execution_start", "toolCallId": "tool:1784970149400:bbbbbbbbbbb",
             "toolName": "get_study_report", "args": {"study_id": 222}},
            {"type": "tool_execution_end", "toolCallId": "tool:1784970149356:aaaaaaaaaaa",
             "toolName": "get_study_report", "isError": False,
             "result": {"content": [], "details": {"detail": "report 111", "ui_payload": {"study_id": 111}}}},
            {"type": "tool_execution_end", "toolCallId": "tool:1784970149400:bbbbbbbbbbb",
             "toolName": "get_study_report", "isError": False,
             "result": {"content": [], "details": {"detail": "report 222", "ui_payload": {"study_id": 222}}}},
        ]
        calls = [p for n, p in translate(events) if n == "segment_tool_call"]
        results = [p for n, p in translate(events) if n == "segment_tool_result"]

        assert calls[0]["name"] != calls[1]["name"]
        # each result must correlate back to its OWN call, not the other one
        assert {r["name"] for r in results} == {c["name"] for c in calls}
        by_name = {r["name"]: r for r in results}
        assert by_name[calls[0]["name"]]["ui_payload"]["study_id"] == 111
        assert by_name[calls[1]["name"]]["ui_payload"]["study_id"] == 222

        segments = build_segments(events)
        tool_segs = [s for s in segments if s["type"] == "tool"]
        assert len(tool_segs) == 2
        assert tool_segs[0]["result"]["ui_payload"]["study_id"] == 111
        assert tool_segs[1]["result"]["ui_payload"]["study_id"] == 222

    def test_tool_result_carries_flask_ui_payload_and_timing_suffix(self, sample_events):
        result_payload = next(p for n, p in translate(sample_events) if n == "segment_tool_result")
        assert result_payload["ui_payload"]["tool"] == "search_studies"
        assert result_payload["ui_payload"]["result_studies"][0]["study_id"] == 11043
        assert result_payload["label"] == "Searched Qiita database"
        assert result_payload["detail"].startswith("top 1 results · ")
        assert result_payload["detail"].endswith("s")  # "· {t:.1f}s" suffix

    def test_text_deltas_become_token_events_in_order_and_concatenate_correctly(self, sample_events):
        tokens = [p["token"] for n, p in translate(sample_events) if n == "token"]
        assert len(tokens) == 5
        assert "".join(tokens) == "Study 11043 (Wild mice gut microbiome survey) matches your query well."

    def test_no_done_event_is_ever_emitted(self, sample_events):
        """translate() never emits done — that's the route's job after the
        generator is exhausted."""
        names = [name for name, _ in translate(sample_events)]
        assert "done" not in names

    def test_toolcall_and_thinking_deltas_produce_no_sse(self, sample_events):
        """Only text_delta becomes a token event; toolcall_start/delta/end and
        message_start/end/turn_start/turn_end/agent_end/agent_settled have no
        SSE equivalent today and must not silently invent one."""
        seq = list(translate(sample_events))
        assert len(seq) == 1 + 2 + 5  # agent_start + (tool_call, tool_result) + 5 tokens


class TestTranslateErrorPaths:
    def test_tool_execution_end_is_error_produces_failure_shape(self):
        events = [
            {"type": "tool_execution_start", "toolCallId": "abcdef1234", "toolName": "search_studies", "args": {}},
            {
                "type": "tool_execution_end", "toolCallId": "abcdef1234", "toolName": "search_studies",
                "isError": True,
                "result": {"content": [{"type": "text", "text": "search_studies failed: 500 db down"}], "details": {}},
            },
        ]
        seq = list(translate(events))
        result_payload = next(p for n, p in seq if n == "segment_tool_result")
        assert "failed" in result_payload["label"]
        assert result_payload["ui_payload"] is None
        assert "db down" in result_payload["detail"]

    def test_sidecar_error_maps_to_error_event(self):
        events = [{"type": "sidecar_error", "error": "sidecar crashed"}]
        seq = list(translate(events))
        assert ("error", {"error": "sidecar crashed"}) in seq

    def test_assistant_message_error_maps_to_error_event(self):
        events = [{
            "type": "message_update",
            "assistantMessageEvent": {"type": "error", "reason": "error", "error": {"errorMessage": "provider exploded"}},
        }]
        seq = list(translate(events))
        assert ("error", {"error": "provider exploded"}) in seq


class TestCompactionAndRetrySteps:
    def test_compaction_maps_to_step_start_and_step_done(self):
        events = [
            {"type": "compaction_start", "reason": "threshold"},
            {"type": "compaction_end", "reason": "threshold", "aborted": False, "willRetry": False,
             "result": {"tokensBefore": 50000, "estimatedTokensAfter": 12000}},
        ]
        seq = list(translate(events))
        assert ("step_start", {"name": "compaction", "label": "Compacting conversation history…"}) in seq
        detail = next(p for n, p in seq if n == "step_done" and p["name"] == "compaction")
        assert detail["detail"] == "50000 → 12000 tokens"

    def test_auto_retry_maps_to_step_start_and_step_done(self):
        events = [
            {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 2000, "errorMessage": "rate limited"},
            {"type": "auto_retry_end", "success": True, "attempt": 1},
        ]
        seq = list(translate(events))
        names = [n for n, _ in seq]
        assert "step_start" in names and "step_done" in names
        done = next(p for n, p in seq if n == "step_done")
        assert done["label"] == "Retry succeeded"


class TestBuildSegments:
    def test_segments_shape_matches_global_chat_routes_convention(self, sample_events):
        segments = build_segments(sample_events)
        types = [s["type"] for s in segments]
        assert types == ["tool", "text"]

        tool_seg = segments[0]
        assert tool_seg["done"] is True
        assert tool_seg["result"]["ui_payload"]["tool"] == "search_studies"
        assert tool_seg["args"] == {"keywords": ["wild", "mice"]}

        text_seg = segments[1]
        assert text_seg["done"] is True
        assert text_seg["content"] == "Study 11043 (Wild mice gut microbiome survey) matches your query well."

    def test_correlation_name_matches_translate(self, sample_events):
        """The call<->result join key must be identical whether computed by
        translate() (live SSE) or build_segments() (persistence) — this is
        exactly the invariant that prevents the two authors from drifting."""
        call_payload = next(p for n, p in translate(sample_events) if n == "segment_tool_call")
        segments = build_segments(sample_events)
        assert segments[0]["name"] == call_payload["name"]

    def test_persisted_detail_keeps_the_live_elapsed_time(self, sample_events):
        """The live SSE detail and the persisted segment detail must be the same
        string, elapsed-time suffix included.

        Regression: segments used to be rebuilt in a second pass after the stream
        had finished, so their timer measured the replay rather than the tool
        call and every reloaded tool card read "· 0.0s" while the live stream had
        shown the real duration. Asserting only that the suffix ends in "s" —
        which is all the older test did — passes happily on "· 0.0s"."""
        import time

        def slow(events):
            for e in events:
                if e.get("type") == "tool_execution_end":
                    time.sleep(0.25)
                yield e

        translator = TurnTranslator()
        live = [p for n, p in translator.run(slow(sample_events)) if n == "segment_tool_result"]
        persisted = [s["result"] for s in translator.segments if s.get("type") == "tool"]

        assert len(live) == len(persisted) == 1
        assert live[0]["detail"] == persisted[0]["detail"]
        # and it recorded the real duration, not the replay
        assert "0.0s" not in persisted[0]["detail"]
        assert re.search(r"· \d+\.\ds$", persisted[0]["detail"])

    def test_failed_tool_call_still_produces_a_done_segment(self):
        events = [
            {"type": "tool_execution_start", "toolCallId": "abcdef1234", "toolName": "get_study_report", "args": {"study_id": 1}},
            {
                "type": "tool_execution_end", "toolCallId": "abcdef1234", "toolName": "get_study_report",
                "isError": True,
                "result": {"content": [{"type": "text", "text": "get_study_report failed: boom"}], "details": {}},
            },
        ]
        segments = build_segments(events)
        assert len(segments) == 1
        assert segments[0]["done"] is True
        assert segments[0]["result"]["ui_payload"] is None
        assert "failed" in segments[0]["result"]["label"]

    def test_no_dangling_text_segment_when_turn_ends_mid_stream(self):
        """flush_text() at the end must still close out any trailing text."""
        events = [
            {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "partial answer"}},
        ]
        segments = build_segments(events)
        assert segments == [{"type": "text", "content": "partial answer", "done": True}]
