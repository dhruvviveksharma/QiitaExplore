"""Durable turn persistence: the user row lands before the turn runs, and
a Stop (GeneratorExit) or mid-turn error persists whatever partial assistant
output accumulated — previously an aborted/failed turn lost everything.
"""
import importlib

import pytest

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()


@pytest.fixture
def chat_turn_mod(fresh_db):
    """helpers.chat_turn re-imported so its store bindings target THIS test's
    temp DB (fresh_db purges store* modules; helpers.* keep stale bindings)."""
    import helpers.chat_turn as ct
    return importlib.reload(ct)


@pytest.fixture
def global_chat(global_chat_crud, sample_user_id):
    chat = global_chat_crud.create_global_chat(sample_user_id, "durability test")
    return chat["chat_id"]


def _noop_build_context():
    return None
    yield  # pragma: no cover — makes this a generator with a plain return


def _turn(chat_turn_mod, chat_id, user_id, agent_events, user_content="hello"):
    """A stream_chat_turn generator whose stream_agent is a scripted fake."""
    def fake_stream_agent(*a, **kw):
        yield from agent_events

    chat_turn_mod.stream_agent = fake_stream_agent
    return chat_turn_mod.stream_chat_turn(
        scope="global", chat_id=chat_id, user_id=user_id, model="minimax-m2",
        user_content=user_content, report_study_id=None, pin_study_ids=None,
        system_prompt="sp", tools=[], full_msgs=[{"role": "user", "content": user_content}],
        persist=lambda ac, up=None: None, build_context=_noop_build_context,
    )


def _messages(global_chat_crud, user_id, chat_id):
    return global_chat_crud.get_global_chat(user_id, chat_id)["messages"]


class TestDurablePersistence:

    def test_normal_completion_persists_user_and_assistant(self, chat_turn_mod, global_chat_crud,
                                                           sample_user_id, global_chat):
        events = [{"type": "agent_start"},
                  {"type": "token", "token": "hi "},
                  {"type": "token", "token": "there"}]
        list(_turn(chat_turn_mod, global_chat, sample_user_id, events))
        msgs = _messages(global_chat_crud, sample_user_id, global_chat)
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[1]["content"] == "hi there"

    def test_abort_mid_stream_persists_partial_turn(self, chat_turn_mod, global_chat_crud,
                                                    sample_user_id, global_chat):
        events = [{"type": "agent_start"},
                  {"type": "token", "token": "partial "},
                  {"type": "token", "token": "answer"},
                  {"type": "token", "token": " never sent"}]
        gen = _turn(chat_turn_mod, global_chat, sample_user_id, events)
        # pull: keepalive, agent_start, first two tokens — then Stop
        for _ in range(4):
            next(gen)
        gen.close()  # raises GeneratorExit inside the generator

        msgs = _messages(global_chat_crud, sample_user_id, global_chat)
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        # keepalive + agent_start + 2 token yields pulled → 2 tokens accumulated
        assert msgs[1]["content"] == "partial answer"
        ui = msgs[1]["ui_payload"]
        assert ui["kind"] == "agent_segments"
        assert ui["segments"][-1]["content"] == "partial answer"

    def test_error_mid_stream_keeps_user_row_and_partial(self, chat_turn_mod, global_chat_crud,
                                                         sample_user_id, global_chat):
        def exploding_events():
            yield {"type": "agent_start"}
            yield {"type": "token", "token": "before the crash"}
            raise RuntimeError("provider blew up")

        def fake_stream_agent(*a, **kw):
            yield from exploding_events()

        chat_turn_mod.stream_agent = fake_stream_agent
        gen = chat_turn_mod.stream_chat_turn(
            scope="global", chat_id=global_chat, user_id=sample_user_id, model="minimax-m2",
            user_content="boom?", report_study_id=None, pin_study_ids=None,
            system_prompt="sp", tools=[], full_msgs=[],
            persist=lambda ac, up=None: None, build_context=_noop_build_context,
        )
        frames = list(gen)  # error is caught and emitted as an SSE error frame
        assert any("event: error" in f for f in frames)

        msgs = _messages(global_chat_crud, sample_user_id, global_chat)
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == "boom?"
        assert msgs[1]["content"] == "before the crash"

    def test_error_before_any_output_still_keeps_user_row(self, chat_turn_mod, global_chat_crud,
                                                          sample_user_id, global_chat):
        def fake_stream_agent(*a, **kw):
            raise RuntimeError("instant failure")
            yield  # pragma: no cover

        chat_turn_mod.stream_agent = fake_stream_agent
        gen = chat_turn_mod.stream_chat_turn(
            scope="global", chat_id=global_chat, user_id=sample_user_id, model="minimax-m2",
            user_content="lost?", report_study_id=None, pin_study_ids=None,
            system_prompt="sp", tools=[], full_msgs=[],
            persist=lambda ac, up=None: None, build_context=_noop_build_context,
        )
        list(gen)
        msgs = _messages(global_chat_crud, sample_user_id, global_chat)
        # user row survives; no empty assistant bubble is fabricated
        assert [m["role"] for m in msgs] == ["user"]
        assert msgs[0]["content"] == "lost?"

    def test_unknown_chat_yields_error_and_persists_nothing(self, chat_turn_mod, global_chat_crud,
                                                            sample_user_id):
        gen = _turn(chat_turn_mod, "no-such-chat", sample_user_id, [])
        frames = list(gen)
        assert any("event: error" in f for f in frames)
        assert global_chat_crud.get_global_chat(sample_user_id, "no-such-chat") is None
