"""Search-budget semantics: up to SEARCH_CALLS_PER_MESSAGE *executed* search
calls per user message; empty-input and crashed calls never consume a slot.
"""
from helpers.agent_tools import ToolResult, _empty_input_result

from .fakes import (
    openai_tool_call_round, openai_text_round,
    make_fake_execute_tool, tool_result,
)


def _collect(gen):
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        return events, stop.value


class TestExecutedFlag:

    def test_executed_defaults_to_true(self):
        assert ToolResult(text="t", label="l").executed is True

    def test_empty_input_helper_marks_not_executed(self):
        assert _empty_input_result("search_studies", "no keywords", "L", "d").executed is False


class TestBudgetAccounting:

    def test_executed_search_consumes_a_slot(self, agent_mod, monkeypatch):
        monkeypatch.setattr(agent_mod, "execute_tool",
                            make_fake_execute_tool(tool_result(executed=True)))
        _, retval = _collect(agent_mod._execute_tool_call(
            "search_studies", {"keywords": ["mouse"]}, "id1",
            scope="global", chat_id="c", deep_search=False, search_calls_used=0))
        assert retval[1] is True

    def test_empty_input_search_does_not_consume_a_slot(self, agent_mod, monkeypatch):
        monkeypatch.setattr(agent_mod, "execute_tool",
                            make_fake_execute_tool(tool_result(executed=False)))
        _, retval = _collect(agent_mod._execute_tool_call(
            "search_studies", {}, "id2",
            scope="global", chat_id="c", deep_search=False, search_calls_used=0))
        assert retval[1] is False

    def test_crashing_search_does_not_consume_a_slot(self, agent_mod, monkeypatch):
        monkeypatch.setattr(agent_mod, "execute_tool",
                            make_fake_execute_tool(RuntimeError("boom")))
        events, retval = _collect(agent_mod._execute_tool_call(
            "search_studies", {"keywords": ["x"]}, "id3",
            scope="global", chat_id="c", deep_search=False, search_calls_used=0))
        assert retval[1] is False
        assert "failed" in events[1]["label"]

    def test_non_search_tool_never_consumes(self, agent_mod, monkeypatch):
        monkeypatch.setattr(agent_mod, "execute_tool",
                            make_fake_execute_tool(tool_result(executed=True)))
        _, retval = _collect(agent_mod._execute_tool_call(
            "get_study_report", {"study_id": 1}, "id4",
            scope="global", chat_id="c", deep_search=False, search_calls_used=0))
        assert retval[1] is False

    def test_short_circuit_past_the_cap(self, agent_mod, monkeypatch):
        # execute_tool must never even be reached
        monkeypatch.setattr(agent_mod, "execute_tool",
                            make_fake_execute_tool(RuntimeError("should not run")))
        events, retval = _collect(agent_mod._execute_tool_call(
            "search_studies", {"keywords": ["x"]}, "id5",
            scope="global", chat_id="c", deep_search=False,
            search_calls_used=agent_mod.SEARCH_CALLS_PER_MESSAGE))
        assert retval[1] is False
        assert "results panel" in retval[0]
        assert events[1]["detail"] == "search limit reached"
        assert events[1]["ui_payload"] is None


class TestBudgetInTheLoop:

    def test_search_tool_stripped_after_budget_exhausted(self, run_turn):
        # budget of 1: round 1 executes a search; round 2's offered tools must
        # no longer include the search tools.
        script = [
            openai_tool_call_round("c1", "search_studies", '{"keywords": ["a"]}'),
            openai_text_round("done"),
        ]
        fake_tool = make_fake_execute_tool(tool_result(executed=True))
        _, client, _ = run_turn(script, fake_tool, search_budget=1)

        round1_tools = [t["function"]["name"] for t in client.calls[0]["tools"]]
        round2_tools = [t["function"]["name"] for t in client.calls[1]["tools"]]
        assert "search_studies" in round1_tools
        assert "search_studies" not in round2_tools
        assert "get_study_report" in round2_tools

    def test_budget_not_burned_by_empty_input_search(self, run_turn):
        # round 1's search returns executed=False → round 2 still offers search.
        script = [
            openai_tool_call_round("c1", "search_studies", '{}'),
            openai_tool_call_round("c2", "search_studies", '{"keywords": ["b"]}'),
            openai_text_round("done"),
        ]
        fake_tool = make_fake_execute_tool(
            tool_result(executed=False), tool_result(executed=True))
        _, client, tool = run_turn(script, fake_tool, search_budget=1)

        round2_tools = [t["function"]["name"] for t in client.calls[1]["tools"]]
        assert "search_studies" in round2_tools
        assert len(tool.calls) == 2  # second search actually executed

    def test_multiple_searches_allowed_within_budget(self, run_turn):
        script = [
            openai_tool_call_round("c1", "search_studies", '{"keywords": ["a"]}'),
            openai_tool_call_round("c2", "search_studies", '{"keywords": ["b"]}'),
            openai_tool_call_round("c3", "search_studies", '{"keywords": ["c"]}'),
            openai_text_round("done"),
        ]
        fake_tool = make_fake_execute_tool(*[tool_result(executed=True)] * 3)
        _, client, tool = run_turn(script, fake_tool, search_budget=5)

        assert len(tool.calls) == 3
        for call in client.calls[:3]:
            assert "search_studies" in [t["function"]["name"] for t in call["tools"]]
