"""Loop-level tests for stream_agent / _stream_anthropic_agent, driven end to
end against scripted fake provider clients — the first coverage of the actual
multi-round iteration behavior (previously only _execute_tool_call had tests).
"""
from .fakes import (
    FakeChunk, FakeChoice, FakeDelta,
    openai_text_round, openai_tool_call_round,
    anthropic_text_round, anthropic_tool_use_round,
    make_fake_execute_tool, tool_result, tokens_of, events_of_type,
)


class TestOpenAITurnLoop:

    def test_two_round_tool_chain_then_text(self, run_turn):
        script = [
            openai_tool_call_round("call_1", "search_studies", '{"keywords": ["mouse"]}'),
            openai_text_round("Here are the results."),
        ]
        fake_tool = make_fake_execute_tool(tool_result(text="found 3 studies"))
        events, client, tool = run_turn(script, fake_tool)

        assert tool.calls == [("search_studies", {"keywords": ["mouse"]})]
        assert tokens_of(events) == "Here are the results."
        types = [e["type"] for e in events]
        assert types[0] == "agent_start"
        assert types.index("segment_tool_call") < types.index("segment_tool_result") < types.index("token")
        assert len(client.calls) == 2

    def test_tool_result_fed_back_to_next_round(self, run_turn):
        script = [
            openai_tool_call_round("call_1", "search_studies", '{"keywords": ["ibd"]}'),
            openai_text_round("done"),
        ]
        fake_tool = make_fake_execute_tool(tool_result(text="TOOL_RESULT_SENTINEL"))
        _, client, _ = run_turn(script, fake_tool)

        round2_msgs = client.calls[1]["messages"]
        assert round2_msgs[-1]["role"] == "tool"
        assert round2_msgs[-1]["content"] == "TOOL_RESULT_SENTINEL"
        assert round2_msgs[-2]["role"] == "assistant"
        assert round2_msgs[-2]["tool_calls"][0]["function"]["name"] == "search_studies"

    def test_max_iters_exhaustion_triggers_forced_synthesis(self, run_turn):
        # Every scripted round demands another tool call; the loop must stop at
        # max_iters and fire one extra no-tools synthesis call for prose.
        script = [openai_tool_call_round(f"call_{i}", "get_study_report", '{"study_id": 1}')
                  for i in range(3)] + [openai_text_round("synthesized")]
        # rounds reuse the LAST entry once exhausted, so pad with the text round
        fake_tool = make_fake_execute_tool(*[tool_result() for _ in range(3)])
        events, client, _ = run_turn(script, fake_tool, max_iters=3)

        assert len(client.calls) == 4  # 3 tool rounds + 1 forced synthesis
        assert "tools" not in client.calls[3]
        assert tokens_of(events) == "synthesized"

    def test_synthesis_not_forced_when_final_round_already_has_text(self, run_turn):
        script = [
            openai_tool_call_round("call_1", "get_study_report", '{"study_id": 5}'),
            openai_text_round("all done"),
        ]
        fake_tool = make_fake_execute_tool(tool_result())
        events, client, _ = run_turn(script, fake_tool)

        assert len(client.calls) == 2  # no extra synthesis call
        assert tokens_of(events) == "all done"

    def test_reasoning_tokens_yielded_before_content(self, run_turn):
        script = [[
            FakeChunk([FakeChoice(FakeDelta(reasoning_content="thinking..."))]),
            FakeChunk([FakeChoice(FakeDelta(content="answer"))]),
            FakeChunk([FakeChoice(FakeDelta(), finish_reason="stop")]),
        ]]
        events, _, _ = run_turn(script, make_fake_execute_tool())

        reasoning = events_of_type(events, "reasoning")
        assert [e["token"] for e in reasoning] == ["thinking..."]
        types = [e["type"] for e in events if e["type"] in ("reasoning", "token")]
        assert types == ["reasoning", "token"]

    def test_heartbeat_chunk_with_empty_choices_is_skipped(self, run_turn):
        script = [[
            FakeChunk([]),  # heartbeat
            FakeChunk([FakeChoice(FakeDelta(content="ok"))]),
            FakeChunk([]),
            FakeChunk([FakeChoice(FakeDelta(), finish_reason="stop")]),
        ]]
        events, _, _ = run_turn(script, make_fake_execute_tool())
        assert tokens_of(events) == "ok"


class TestAnthropicTurnLoop:

    def test_provider_string_selects_anthropic_path(self, run_turn):
        script = [anthropic_text_round("claude says hi")]
        events, client, _ = run_turn(script, make_fake_execute_tool(), provider="anthropic")

        assert tokens_of(events) == "claude says hi"
        # tools were converted to Anthropic shape for the call
        tools_sent = client.calls[0]["tools"]
        assert all("input_schema" in t and "name" in t for t in tools_sent)

    def test_tool_use_json_delta_reassembly(self, run_turn):
        script = [
            anthropic_tool_use_round("toolu_01AAAA", "search_studies",
                                     ['{"keywo', 'rds": ["wild", ', '"mice"]}']),
            anthropic_text_round("done"),
        ]
        fake_tool = make_fake_execute_tool(tool_result())
        _, _, tool = run_turn(script, fake_tool, provider="anthropic")

        assert tool.calls == [("search_studies", {"keywords": ["wild", "mice"]})]

    def test_tool_result_fed_back_in_anthropic_shape(self, run_turn):
        script = [
            anthropic_tool_use_round("toolu_01BBBB", "search_studies", ['{}']),
            anthropic_text_round("done"),
        ]
        fake_tool = make_fake_execute_tool(tool_result(text="ANTH_SENTINEL"))
        _, client, _ = run_turn(script, fake_tool, provider="anthropic")

        round2_msgs = client.calls[1]["messages"]
        tool_result_msg = round2_msgs[-1]
        assert tool_result_msg["role"] == "user"
        blocks = tool_result_msg["content"]
        assert blocks[0]["type"] == "tool_result"
        assert blocks[0]["tool_use_id"] == "toolu_01BBBB"
        assert blocks[0]["content"] == "ANTH_SENTINEL"

    def test_anthropic_forced_synthesis_on_max_iters(self, run_turn):
        script = [
            anthropic_tool_use_round("toolu_01CCCC", "get_study_report", ['{"study_id": 1}']),
            anthropic_text_round("late prose"),
        ]
        fake_tool = make_fake_execute_tool(tool_result())
        events, client, _ = run_turn(script, fake_tool, provider="anthropic", max_iters=1)

        assert len(client.calls) == 2
        assert "tools" not in client.calls[1]
        assert tokens_of(events) == "late prose"


class TestNoSilentStop:
    """A turn must NEVER end without visible text — the guaranteed-fallback
    path added after a live chat ended with tool cards and no final answer."""

    def test_empty_forced_synthesis_yields_fallback_text(self, run_turn):
        # Loop exhausts max_iters on tool calls, then the forced-synthesis
        # call streams zero content — previously the turn just went silent.
        script = [
            openai_tool_call_round("call_1", "get_study_report", '{"study_id": 1}'),
            openai_text_round(""),  # synthesis round: no tokens at all
        ]
        fake_tool = make_fake_execute_tool(tool_result())
        events, client, _ = run_turn(script, fake_tool, max_iters=1)

        assert len(client.calls) == 2  # tool round + (empty) synthesis
        assert "ran out of tool rounds (1)" in tokens_of(events)

    def test_exhaustion_emits_visible_synthesis_step(self, run_turn):
        script = [
            openai_tool_call_round("call_1", "get_study_report", '{"study_id": 1}'),
            openai_text_round("synthesized"),
        ]
        fake_tool = make_fake_execute_tool(tool_result())
        events, _, _ = run_turn(script, fake_tool, max_iters=1)

        steps = [e for e in events_of_type(events, "step_start")
                 if e["name"] == "synthesis"]
        assert len(steps) == 1
        assert "Tool-round limit (1)" in steps[0]["label"]

    def test_fallback_text_names_the_last_tool_error(self, run_turn):
        script = [
            openai_tool_call_round("call_1", "search_studies", '{"keywords": ["x"]}'),
            openai_text_round(""),
        ]
        fake_tool = make_fake_execute_tool(RuntimeError('malformed array literal: "Metagenomic"'))
        events, _, _ = run_turn(script, fake_tool, max_iters=1)

        text = tokens_of(events)
        assert "ran out of tool rounds" in text
        assert 'malformed array literal: "Metagenomic"' in text

    def test_anthropic_empty_synthesis_yields_fallback_text(self, run_turn):
        script = [
            anthropic_tool_use_round("toolu_01DDDD", "get_study_report", ['{"study_id": 1}']),
            anthropic_text_round(""),  # synthesis streams an empty text block
        ]
        fake_tool = make_fake_execute_tool(tool_result())
        events, client, _ = run_turn(script, fake_tool, provider="anthropic", max_iters=1)

        assert len(client.calls) == 2
        assert "ran out of tool rounds (1)" in tokens_of(events)

    def test_normal_turn_gets_no_fallback_text(self, run_turn):
        script = [openai_text_round("plain answer")]
        events, _, _ = run_turn(script, make_fake_execute_tool())

        text = tokens_of(events)
        assert text == "plain answer"
        assert "ran out of tool rounds" not in text

    def test_empty_response_gets_reason_aware_wording(self, run_turn):
        # The model returns nothing on round 0 with no tool calls at all —
        # round exhaustion never happened, so the fallback must not claim it.
        script = [openai_text_round("")]
        events, _, _ = run_turn(script, make_fake_execute_tool())

        text = tokens_of(events)
        assert "empty response" in text
        assert "ran out of tool rounds" not in text

    def test_anthropic_empty_response_gets_reason_aware_wording(self, run_turn):
        script = [anthropic_text_round("")]
        events, _, _ = run_turn(script, make_fake_execute_tool(), provider="anthropic")

        text = tokens_of(events)
        assert "empty response" in text
        assert "ran out of tool rounds" not in text
