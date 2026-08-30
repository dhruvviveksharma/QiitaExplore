"""Cross-turn tool memory: the per-turn tool exchange persists as a normalized
model_transcript and replays in either provider's wire shape on later turns —
previously the model had zero memory of prior tool calls/results.
"""
import importlib

import pytest

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()

from helpers.chat_transcript import (  # noqa: E402
    entry_to_anthropic_messages,
    entry_to_openai_messages,
    rows_to_provider_messages,
    truncate_for_persist,
)

ASSISTANT_ENTRY = {"role": "assistant", "text": "let me search",
                   "tool_calls": [{"id": "call_1", "name": "search_studies",
                                   "args": {"keywords": ["ibd"]}}]}
TOOL_ENTRY = {"role": "tool", "id": "call_1", "name": "search_studies",
              "text": "found study 101"}


class TestWireConversion:

    def test_openai_assistant_shape(self):
        (msg,) = entry_to_openai_messages(ASSISTANT_ENTRY)
        assert msg["role"] == "assistant" and msg["content"] == "let me search"
        tc = msg["tool_calls"][0]
        assert tc["id"] == "call_1" and tc["type"] == "function"
        assert tc["function"]["name"] == "search_studies"
        assert tc["function"]["arguments"] == '{"keywords": ["ibd"]}'

    def test_openai_tool_shape(self):
        (msg,) = entry_to_openai_messages(TOOL_ENTRY)
        assert msg == {"role": "tool", "tool_call_id": "call_1", "content": "found study 101"}

    def test_anthropic_assistant_shape(self):
        (msg,) = entry_to_anthropic_messages(ASSISTANT_ENTRY)
        assert msg["role"] == "assistant"
        assert msg["content"][0] == {"type": "text", "text": "let me search"}
        assert msg["content"][1] == {"type": "tool_use", "id": "call_1",
                                     "name": "search_studies", "input": {"keywords": ["ibd"]}}

    def test_anthropic_tool_shape(self):
        (msg,) = entry_to_anthropic_messages(TOOL_ENTRY)
        assert msg["role"] == "user"
        assert msg["content"][0] == {"type": "tool_result", "tool_use_id": "call_1",
                                     "content": "found study 101"}

    def test_same_transcript_replays_in_both_shapes(self):
        """The provider-switch case: identical stored rows must produce valid
        messages for either provider with identical tool args/results."""
        rows = [{"id": 1, "role": "user", "content": "find ibd", "model_transcript": None},
                {"id": 2, "role": "assistant", "content": "Found it.",
                 "model_transcript": [ASSISTANT_ENTRY, TOOL_ENTRY]}]
        oa = rows_to_provider_messages(rows, "nrp")
        an = rows_to_provider_messages(rows, "anthropic")
        assert oa[0] == an[0] == {"role": "user", "content": "find ibd"}
        # OpenAI: assistant(tool_calls), tool, assistant text
        assert [m["role"] for m in oa[1:]] == ["assistant", "tool", "assistant"]
        # Anthropic: assistant(tool_use blocks), user(tool_result), assistant text
        assert [m["role"] for m in an[1:]] == ["assistant", "user", "assistant"]
        assert oa[-1]["content"] == an[-1]["content"] == "Found it."


class TestReplayRules:

    def test_legacy_row_without_transcript_replays_text_only(self):
        rows = [{"id": 1, "role": "user", "content": "hi", "model_transcript": None},
                {"id": 2, "role": "assistant", "content": "hello", "model_transcript": None}]
        msgs = rows_to_provider_messages(rows, "nrp")
        assert msgs == [{"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "hello"}]

    def test_empty_assistant_text_with_transcript_skips_the_text_message(self):
        rows = [{"id": 2, "role": "assistant", "content": "",
                 "model_transcript": [ASSISTANT_ENTRY, TOOL_ENTRY]}]
        msgs = rows_to_provider_messages(rows, "nrp")
        assert [m["role"] for m in msgs] == ["assistant", "tool"]

    def test_truncate_for_persist_caps_tool_text_only(self):
        long_tool = {**TOOL_ENTRY, "text": "y" * 5000}
        out = truncate_for_persist([ASSISTANT_ENTRY, long_tool])
        assert out[0] == ASSISTANT_ENTRY
        assert len(out[1]["text"]) < 5000
        assert out[1]["text"].endswith("…(truncated for history)")


class TestPersistedRoundTrip:

    def test_transcript_survives_persist_and_reload(self, fresh_db, global_chat_crud, sample_user_id):
        from store.chat_turn_persist import (append_user_message, append_assistant_message,
                                             load_turn_rows)
        chat = global_chat_crud.create_global_chat(sample_user_id, "t")
        chat_id = chat["chat_id"]
        append_user_message("global", chat_id, sample_user_id, "find ibd")
        append_assistant_message("global", chat_id, "Found it.",
                                 model_transcript=[ASSISTANT_ENTRY, TOOL_ENTRY])
        rows = load_turn_rows(chat_id, "global")
        assert rows[0]["role"] == "user" and rows[0]["model_transcript"] is None
        assert rows[1]["model_transcript"] == [ASSISTANT_ENTRY, TOOL_ENTRY]

    def test_second_turn_model_sees_first_turns_tool_results(self, fresh_db, global_chat_crud,
                                                             sample_user_id, monkeypatch):
        """End-to-end memory: turn 1 runs a tool; turn 2's provider request must
        contain turn 1's tool result."""
        # reload the chain: chat_history first (its store bindings must target
        # THIS test's temp DB), then chat_turn (which re-imports it).
        import helpers.chat_history as chm
        importlib.reload(chm)
        import helpers.chat_turn as ct
        ct = importlib.reload(ct)
        import helpers.agent as agent_mod
        from tests.agent.fakes import (FakeOpenAIClient, openai_tool_call_round,
                                       openai_text_round, tool_result,
                                       make_fake_execute_tool)

        chat = global_chat_crud.create_global_chat(sample_user_id, "mem")
        chat_id = chat["chat_id"]

        def _noop_ctx():
            return None
            yield  # pragma: no cover

        def run(script, execute_tool, message):
            client = FakeOpenAIClient(script)
            monkeypatch.setattr(agent_mod, "get_client", lambda m: (client, "nrp"))
            monkeypatch.setattr(agent_mod, "execute_tool", execute_tool)
            list(ct.stream_chat_turn(
                scope="global", chat_id=chat_id, user_id=sample_user_id,
                model="minimax-m2", user_content=message,
                report_study_id=None, pin_study_ids=None, system_prompt="sp",
                tools=[{"type": "function", "function": {"name": "search_studies", "parameters": {}}}],
                full_msgs=[], persist=lambda ac, up=None: None, build_context=_noop_ctx))
            return client

        # turn 1: search happens
        run([openai_tool_call_round("call_T1", "search_studies", '{"keywords": ["ibd"]}'),
             openai_text_round("Found 3 studies.")],
            make_fake_execute_tool(tool_result(text="TOOL_MEMORY_SENTINEL")),
            "find ibd studies")

        # turn 2: no tools — inspect what the provider was sent
        client2 = run([openai_text_round("It was study 101.")],
                      make_fake_execute_tool(), "what was the 3rd result?")
        sent = client2.calls[0]["messages"]
        roles = [m["role"] for m in sent]
        assert roles[0] == "system"
        assert "tool" in roles, f"prior tool exchange missing from replay: {roles}"
        tool_msg = next(m for m in sent if m["role"] == "tool")
        assert tool_msg["content"] == "TOOL_MEMORY_SENTINEL"
        assert sent[-1] == {"role": "user", "content": "what was the 3rd result?"}
