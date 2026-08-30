"""History compaction: over-budget chats summarize older turns with the same
model, persist an anchor, and never re-summarize what a prior compaction
already covered.
"""
import importlib

import pytest


@pytest.fixture
def chat_history_mod(fresh_db):
    """Reload so store bindings target this test's temp DB (fresh_db purges
    store*; helpers.* keep stale bindings otherwise)."""
    import helpers.chat_history as ch
    return importlib.reload(ch)


@pytest.fixture
def seeded_chat(global_chat_crud, sample_user_id):
    """A global chat with 6 completed turns of ~2,100 chars each — big enough
    to clear the 8,000-char budget floor in _history_budget_chars."""
    from store.chat_turn_persist import append_user_message, append_assistant_message
    chat = global_chat_crud.create_global_chat(sample_user_id, "long chat")
    chat_id = chat["chat_id"]
    for i in range(6):
        append_user_message("global", chat_id, sample_user_id, f"question {i} " + "q" * 900)
        append_assistant_message("global", chat_id, f"answer {i} " + "a" * 1200)
    return chat_id


def _drive(gen):
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            return events, stop.value


class TestUnits:

    def test_pair_rows_into_turns(self, chat_history_mod):
        rows = [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"},
                {"role": "assistant", "content": "a2b"}]
        turns = chat_history_mod._pair_rows_into_turns(rows)
        assert [len(t) for t in turns] == [2, 3]
        assert turns[1][0]["content"] == "u2"

    def test_serialize_includes_tool_exchange(self, chat_history_mod):
        turns = [[{"role": "user", "content": "find x"},
                  {"role": "assistant", "content": "done",
                   "model_transcript": [
                       {"role": "assistant", "text": "", "tool_calls":
                        [{"id": "c1", "name": "search_studies", "args": {"keywords": ["x"]}}]},
                       {"role": "tool", "id": "c1", "name": "search_studies", "text": "hit 101"}]}]]
        text = chat_history_mod._serialize_turns(turns)
        assert "[User]: find x" in text
        assert 'search_studies({"keywords": ["x"]})' in text
        assert "[Tool result search_studies]: hit 101" in text
        assert "[Assistant]: done" in text


class TestCompaction:

    def _prepare(self, chat_history_mod, chat_id, monkeypatch, summaries):
        """Run prepare_history with a tiny budget and a fake summarizer."""
        # Force the trigger: total budget floor is 8000 chars; make the keep
        # window tiny so most turns fall into "older".
        monkeypatch.setattr(chat_history_mod, "context_budget_chars", lambda m: 8_000)
        monkeypatch.setattr(chat_history_mod.config, "HISTORY_COMPACTION_RESERVE_TOKENS", 0)
        monkeypatch.setattr(chat_history_mod.config, "HISTORY_KEEP_VERBATIM_TOKENS", 300)

        def fake_llm_chat(messages, study_context_text, system_prompt, model=None):
            summaries.append(messages[0]["content"])
            return f"SUMMARY #{len(summaries)}"

        monkeypatch.setattr(chat_history_mod, "llm_chat", fake_llm_chat)
        return _drive(chat_history_mod.prepare_history(
            chat_id, "global", "minimax-m2", "sp", None))

    def test_under_budget_is_a_no_op(self, chat_history_mod, seeded_chat, monkeypatch):
        called = []
        monkeypatch.setattr(chat_history_mod, "llm_chat",
                            lambda *a, **k: called.append(1))
        events, (rows, summary) = _drive(chat_history_mod.prepare_history(
            seeded_chat, "global", "minimax-m2", "sp", None))
        assert events == [] and summary is None and called == []
        assert len(rows) == 12  # 6 turns × 2 rows

    def test_over_budget_summarizes_and_anchors(self, chat_history_mod, seeded_chat, monkeypatch):
        from store.chat_turn_persist import get_compaction_state
        summaries = []
        events, (rows, summary) = self._prepare(chat_history_mod, seeded_chat,
                                                monkeypatch, summaries)
        assert [e["type"] for e in events] == ["step_start", "step_done"]
        assert summary == "SUMMARY #1"
        assert len(summaries) == 1 and "question 0" in summaries[0]
        state = get_compaction_state(seeded_chat, "global")
        assert state["summary"] == "SUMMARY #1"
        # anchor sits on the last summarized row; kept rows all come after it
        assert all(r["id"] > state["through_id"] for r in rows)
        assert len(rows) < 12

    def test_recompaction_reanchors_not_resummarizes(self, chat_history_mod, seeded_chat,
                                                     monkeypatch, global_chat_crud,
                                                     sample_user_id):
        from store.chat_turn_persist import (append_user_message, append_assistant_message,
                                             get_compaction_state)
        summaries = []
        self._prepare(chat_history_mod, seeded_chat, monkeypatch, summaries)
        first_anchor = get_compaction_state(seeded_chat, "global")["through_id"]

        # grow the chat further, then compact again
        for i in range(6, 10):
            append_user_message("global", seeded_chat, sample_user_id, f"question {i} " + "q" * 900)
            append_assistant_message("global", seeded_chat, f"answer {i} " + "a" * 1200)
        events, (rows, summary) = self._prepare(chat_history_mod, seeded_chat,
                                                monkeypatch, summaries)
        assert summary == "SUMMARY #2"
        # the second summarization saw the prior summary + only newer turns —
        # never the raw text of already-summarized turns
        assert "SUMMARY #1" in summaries[1]
        assert "question 0" not in summaries[1]
        second_anchor = get_compaction_state(seeded_chat, "global")["through_id"]
        assert second_anchor > first_anchor


class TestSummaryReachesTheModel:

    def test_summary_lands_in_the_system_message(self, fresh_db, global_chat_crud,
                                                 sample_user_id, monkeypatch):
        import helpers.chat_turn as ct
        ct = importlib.reload(ct)
        import helpers.agent as agent_mod
        from tests.agent.fakes import FakeOpenAIClient, openai_text_round, make_fake_execute_tool

        chat = global_chat_crud.create_global_chat(sample_user_id, "s")
        client = FakeOpenAIClient([openai_text_round("ok")])
        monkeypatch.setattr(agent_mod, "get_client", lambda m: (client, "nrp"))
        monkeypatch.setattr(agent_mod, "execute_tool", make_fake_execute_tool())

        def fake_prepare(chat_id, scope, model, system_prompt, context_block, until_id=None):
            return [], "PINNED SUMMARY SENTINEL"
            yield  # pragma: no cover

        monkeypatch.setattr(ct, "prepare_history", fake_prepare)

        def _noop_ctx():
            return None
            yield  # pragma: no cover

        list(ct.stream_chat_turn(
            scope="global", chat_id=chat["chat_id"], user_id=sample_user_id,
            model="minimax-m2", user_content="hello",
            report_study_id=None, pin_study_ids=None, system_prompt="sp",
            tools=[], full_msgs=[], persist=lambda ac, up=None: None,
            build_context=_noop_ctx))

        system = client.calls[0]["messages"][0]
        assert system["role"] == "system"
        assert "EARLIER CONVERSATION (compacted summary):" in system["content"]
        assert "PINNED SUMMARY SENTINEL" in system["content"]
