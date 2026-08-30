"""Correlation-id regression: step names must carry the FULL tool_call_id.

Anthropic ids all share the 6-char prefix "toolu_", so the old
f"tool_{name}_{call_id[:6]}" collapsed every multi-tool-call turn onto one
step name — and the routes/frontend correlate results to calls purely by that
string, silently attaching one call's result to another call's card.
"""
from .fakes import (
    openai_two_tool_calls_round, openai_text_round,
    make_fake_execute_tool, tool_result, events_of_type,
)


def test_full_call_id_appears_in_step_name(agent_mod, monkeypatch):
    monkeypatch.setattr(agent_mod, "execute_tool", make_fake_execute_tool(tool_result()))
    events = []
    gen = agent_mod._execute_tool_call(
        "get_study_report", {"study_id": 1}, "abc123def456",
        scope="global", chat_id="c", deep_search=False, search_calls_used=0)
    try:
        while True:
            events.append(next(gen))
    except StopIteration:
        pass
    assert events[0]["name"] == "tool_get_study_report_abc123def456"
    assert events[1]["name"] == "tool_get_study_report_abc123def456"


def test_two_calls_to_the_same_tool_do_not_collide(run_turn):
    # Two parallel calls whose ids share a 6-char prefix — the exact collision
    # class the [:6] truncation produced.
    script = [
        openai_two_tool_calls_round("toolu_AAAA_1", "toolu_AAAA_2",
                                    "get_study_report", '{"study_id": 7}'),
        openai_text_round("done"),
    ]
    fake_tool = make_fake_execute_tool(
        tool_result(ui_payload={"which": "first"}),
        tool_result(ui_payload={"which": "second"}),
    )
    events, _, _ = run_turn(script, fake_tool)

    calls = events_of_type(events, "segment_tool_call")
    results = events_of_type(events, "segment_tool_result")
    assert len(calls) == 2 and len(results) == 2
    assert calls[0]["name"] != calls[1]["name"]
    # each result correlates to its own call and carries its own payload
    by_name = {r["name"]: r for r in results}
    assert by_name[calls[0]["name"]]["ui_payload"] == {"which": "first"}
    assert by_name[calls[1]["name"]]["ui_payload"] == {"which": "second"}


def test_ids_shorter_than_six_chars_still_work(agent_mod, monkeypatch):
    monkeypatch.setattr(agent_mod, "execute_tool", make_fake_execute_tool(tool_result()))
    events = []
    gen = agent_mod._execute_tool_call(
        "pin_study", {"study_ids": [1]}, "ab1",
        scope="global", chat_id="c", deep_search=False, search_calls_used=0)
    try:
        while True:
            events.append(next(gen))
    except StopIteration:
        pass
    assert events[0]["name"] == "tool_pin_study_ab1"
