"""Slash-command steering (helpers/forced_tool.py) through the real agent loop:
forced rounds offer only the forced tool, the call always happens (synthesized
when the model ignores it), forced args win, and a command without a study id
goes through resolve_study first."""
import openai
import pytest

from helpers.agent_tool_schemas import STUDY_TOOL_SCHEMAS
from helpers.forced_tool import ForcedPlan

from .fakes import (
    anthropic_text_round, anthropic_tool_use_round, make_fake_execute_tool, openai_text_round,
    openai_tool_call_round, tokens_of, tool_result,
)

SEARCH = {"type": "function", "function": {"name": "search_studies", "parameters": {"type": "object", "properties": {}}}}
TOOLS = [SEARCH] + STUDY_TOOL_SCHEMAS
GRAPH = {"name": "get_prep_graph", "args": {"study_id": 10317, "prep_id": 1115}, "text": ""}
RESOLVED = tool_result(text="Resolved to study 10317", ui_payload={"kind": "study_resolved", "study_id": 10317})
AMBIGUOUS = tool_result(text="Not certain", ui_payload={"kind": "study_choice", "candidates": []})


def _names(call):
    return [t["function"]["name"] for t in call.get("tools", [])]


def _calls(events):
    return [e for e in events if e["type"] == "segment_tool_call"]


class TestOpenAIForced:

    def test_round_zero_offers_only_the_forced_tool_then_all(self, run_turn):
        script = [openai_tool_call_round("c1", "get_prep_graph", '{"study_id": 10317, "prep_id": 1115}'),
                  openai_text_round("It has 54 nodes.")]
        events, client, tool = run_turn(script, make_fake_execute_tool(tool_result()), tools=TOOLS, force_tool=GRAPH)
        assert _names(client.calls[0]) == ["get_prep_graph"] and client.calls[0]["tool_choice"] == "required"
        assert len(_names(client.calls[1])) == len(TOOLS) and "tool_choice" not in client.calls[1]
        assert tool.calls == [("get_prep_graph", {"study_id": 10317, "prep_id": 1115})]
        assert tokens_of(events) == "It has 54 nodes."

    def test_hint_is_on_the_live_user_turn(self, run_turn):
        script = [openai_tool_call_round("c1", "get_prep_graph", "{}"), openai_text_round("ok")]
        _, client, _ = run_turn(script, make_fake_execute_tool(tool_result()), tools=TOOLS, force_tool=GRAPH,
                                messages=[{"role": "user", "content": "/graph 10317 1115"}])
        user = [m for m in client.calls[0]["messages"] if m["role"] == "user"][-1]   # the list grows after round 0
        assert user["content"].startswith("/graph 10317 1115\n\n[Slash command: call get_prep_graph")

    def test_forced_args_win_and_history_matches(self, run_turn):
        script = [openai_tool_call_round("c1", "get_prep_graph", '{"study_id": 1, "prep_id": 2}'), openai_text_round("ok")]
        events, client, tool = run_turn(script, make_fake_execute_tool(tool_result()), tools=TOOLS, force_tool=GRAPH)
        assert tool.calls[0][1] == {"study_id": 10317, "prep_id": 1115}
        asst = client.calls[1]["messages"][-2]
        assert asst["tool_calls"][0]["function"]["arguments"] == '{"study_id": 10317, "prep_id": 1115}'

    def test_model_that_ignores_the_tool_gets_a_synthesized_call(self, run_turn):
        script = [openai_text_round("Let me think about AGP…"), openai_text_round("Here it is.")]
        events, client, tool = run_turn(script, make_fake_execute_tool(tool_result()), tools=TOOLS, force_tool=GRAPH)
        assert tool.calls == [("get_prep_graph", {"study_id": 10317, "prep_id": 1115})]
        assert _calls(events)[0]["name"].startswith("tool_get_prep_graph_force_")
        assert tokens_of(events) == "Here it is."                   # the round-0 text is never shown
        assert client.calls[1]["messages"][-2]["content"] is None   # nor kept in the replayed assistant turn

    def test_tool_call_finishing_with_stop_still_runs(self, run_turn):
        rnd = openai_tool_call_round("c1", "get_prep_graph", "{}")
        rnd[-1].choices[0].finish_reason = "stop"
        events, _, tool = run_turn([rnd, openai_text_round("ok")], make_fake_execute_tool(tool_result()),
                                   tools=TOOLS, force_tool=GRAPH)
        assert [c[0] for c in tool.calls] == ["get_prep_graph"] and _calls(events)[0]["name"] == "tool_get_prep_graph_c1"

    @pytest.mark.parametrize("flag, want", [("required", "required"),
                                            ("named", {"type": "function", "function": {"name": "get_prep_graph"}}),
                                            (None, None)])
    def test_tool_choice_only_where_the_model_supports_it(self, run_turn, agent_mod, monkeypatch, flag, want):
        monkeypatch.setitem(agent_mod.MODEL_METADATA, "minimax-m2", {**agent_mod.MODEL_METADATA["minimax-m2"],
                                                                     "forced_tool_choice": flag})
        script = [openai_tool_call_round("c1", "get_prep_graph", "{}"), openai_text_round("ok")]
        _, client, _ = run_turn(script, make_fake_execute_tool(tool_result()), tools=TOOLS, force_tool=GRAPH)
        assert client.calls[0].get("tool_choice") == want and "tool_choice" not in client.calls[1]

    def test_a_rejected_tool_choice_is_retried_without_it(self, run_turn, agent_mod, monkeypatch):
        monkeypatch.setitem(agent_mod.MODEL_METADATA, "minimax-m2", {**agent_mod.MODEL_METADATA["minimax-m2"],
                                                                     "forced_tool_choice": "named"})
        import httpx
        rejected = openai.BadRequestError("tool_choice not supported", body=None,
                                          response=httpx.Response(400, request=httpx.Request("POST", "http://x")))
        script = [rejected, openai_tool_call_round("c1", "get_prep_graph", "{}"), openai_text_round("ok")]
        _, client, tool = run_turn(script, make_fake_execute_tool(tool_result()), tools=TOOLS, force_tool=GRAPH)
        assert "tool_choice" in client.calls[0] and "tool_choice" not in client.calls[1]
        assert [c[0] for c in tool.calls] == ["get_prep_graph"]

    def test_without_a_study_id_resolve_then_the_target(self, run_turn):
        force = {"name": "get_study_preps", "args": {}, "text": "get me preps related to AGP"}
        script = [openai_tool_call_round("c1", "resolve_study", '{"text": "AGP"}'),
                  openai_text_round("…"),                                    # ignores the forced target
                  openai_text_round("AGP has 308 preps.")]
        events, client, tool = run_turn(script, make_fake_execute_tool(RESOLVED, tool_result()), tools=TOOLS, force_tool=force)
        assert _names(client.calls[0]) == ["resolve_study"] and _names(client.calls[1]) == ["get_study_preps"]
        assert tool.calls == [("resolve_study", {"text": "get me preps related to AGP", "for_tool": "get_study_preps"}),
                              ("get_study_preps", {"study_id": 10317})]
        assert tokens_of(events) == "AGP has 308 preps."

    def test_ambiguous_resolve_asks_without_tools_and_never_calls_the_target(self, run_turn):
        force = {"name": "get_study_preps", "args": {}, "text": "AGP"}
        script = [openai_tool_call_round("c1", "resolve_study", "{}"), openai_text_round("Which of these did you mean?")]
        events, client, tool = run_turn(script, make_fake_execute_tool(AMBIGUOUS), tools=TOOLS, force_tool=force)
        assert [c[0] for c in tool.calls] == ["resolve_study"]
        assert "tools" not in client.calls[1] and len(client.calls) == 2
        assert tokens_of(events) == "Which of these did you mean?"


    def test_aggregations_command_needs_no_study(self, run_turn):
        force = {"name": "list_aggregations", "args": {"name": "Gut cohort"}, "text": ""}
        script = [openai_tool_call_round("c1", "list_aggregations", "{}"), openai_text_round("You have one.")]
        _, client, tool = run_turn(script, make_fake_execute_tool(tool_result()), tools=TOOLS, force_tool=force)
        assert _names(client.calls[0]) == ["list_aggregations"]
        assert tool.calls == [("list_aggregations", {"name": "Gut cohort"})]

    def test_aggregate_by_name_resolves_then_adds_to_the_chat_aggregation(self, run_turn):
        force = {"name": "add_to_chat_aggregation", "args": {}, "text": "AGP 16S"}
        script = [openai_tool_call_round("c1", "resolve_study", "{}"),
                  openai_tool_call_round("c2", "add_to_chat_aggregation", '{"data_types": ["16S"]}'),
                  openai_text_round("Added AGP's 16S samples.")]
        _, client, tool = run_turn(script, make_fake_execute_tool(RESOLVED, tool_result()), tools=TOOLS, force_tool=force)
        assert [c[0] for c in tool.calls] == ["resolve_study", "add_to_chat_aggregation"]
        assert tool.calls[1][1] == {"data_types": ["16S"], "study_id": 10317}


class TestAnthropicForced:

    def test_tool_choice_names_the_tool_and_text_is_hidden(self, run_turn):
        rnd = anthropic_tool_use_round("tu1", "get_prep_graph", ['{"prep_id": 9}'])
        script = [anthropic_text_round("preamble") [:3] + rnd, anthropic_text_round("Done.")]
        events, client, tool = run_turn(script, make_fake_execute_tool(tool_result()), provider="anthropic",
                                        tools=TOOLS, force_tool=GRAPH)
        first = client.calls[0]
        assert first["tool_choice"] == {"type": "tool", "name": "get_prep_graph", "disable_parallel_tool_use": True}
        assert [t["name"] for t in first["tools"]] == ["get_prep_graph"] and "tool_choice" not in client.calls[1]
        assert tool.calls == [("get_prep_graph", {"study_id": 10317, "prep_id": 1115})]
        assert tokens_of(events) == "Done."

    def test_synthesized_when_no_tool_use(self, run_turn):
        events, _, tool = run_turn([anthropic_text_round("hmm"), anthropic_text_round("Done.")],
                                   make_fake_execute_tool(tool_result()), provider="anthropic",
                                   tools=TOOLS, force_tool=GRAPH)
        assert tool.calls == [("get_prep_graph", {"study_id": 10317, "prep_id": 1115})]
        assert tokens_of(events) == "Done."


def test_plan_steps():
    p = ForcedPlan({"name": "get_prep_graph", "args": {}, "text": "AGP graph"})
    assert p.current() == "resolve_study" and p.forced_args("resolve_study") == {"text": "AGP graph", "for_tool": "get_prep_graph"}
    p.done("get_prep_graph", None)                    # not the current step: ignored
    assert p.current() == "resolve_study"
    p.done("resolve_study", {"kind": "study_resolved", "study_id": 7})
    assert p.current() == "get_prep_graph" and p.forced_args("get_prep_graph") == {"study_id": 7}
    p.done("get_prep_graph", {"kind": "prep_graph"})
    assert p.current() is None and p.tools([1, 2], str) == [1, 2]
