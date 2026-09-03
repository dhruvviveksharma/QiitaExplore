"""Durable turn persistence: the user row lands before the turn runs, and
a Stop (GeneratorExit) or mid-turn error persists whatever partial assistant
output accumulated — previously an aborted/failed turn lost everything.
"""
import importlib
import json
import threading

import pytest

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()


@pytest.fixture
def chat_turn_mod(fresh_db):
    """helpers.chat_turn re-imported so its store bindings target THIS test's
    temp DB (fresh_db purges store* modules; helpers.* keep stale bindings).
    chat_history reloads first — chat_turn re-imports it."""
    import helpers.chat_history as chm
    importlib.reload(chm)
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


def _done_payload(frames):
    for frame in frames:
        if frame.startswith("event: done"):
            return json.loads(frame.split("data: ", 1)[1].strip())
    return None


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


@pytest.fixture
def untitled_chat(global_chat_crud, sample_user_id):
    """A chat with no explicit title — still "New chat" — the only state that
    arms the title-generation thread."""
    return global_chat_crud.create_global_chat(sample_user_id)["chat_id"]


@pytest.fixture
def fake_title(monkeypatch):
    """Stub the LLM boundary chat_turn calls through (helpers.chat_title is
    store-free, so it needs no reload — see the module's own docstring)."""
    import helpers.chat_title as title_mod
    calls = []

    def fake_llm_chat(*a, **k):
        calls.append((a, k))
        return "Gut Microbiome Overview"

    monkeypatch.setattr(title_mod, "llm_chat", fake_llm_chat)
    return calls


class TestAutoTitleOnFirstTurn:

    def test_first_turn_done_carries_llm_title(self, chat_turn_mod, global_chat_crud,
                                               sample_user_id, untitled_chat, fake_title):
        events = [{"type": "agent_start"}, {"type": "token", "token": "answer"}]
        frames = list(_turn(chat_turn_mod, untitled_chat, sample_user_id, events,
                            user_content="find gut studies"))
        done = _done_payload(frames)
        assert done["title"] == "Gut Microbiome Overview"
        assert len(fake_title) == 1
        loaded = global_chat_crud.get_global_chat(sample_user_id, untitled_chat)
        assert loaded["title"] == "Gut Microbiome Overview"

    def test_second_turn_makes_no_llm_call_and_keeps_title(self, chat_turn_mod, global_chat_crud,
                                                            sample_user_id, untitled_chat, fake_title):
        events = [{"type": "agent_start"}, {"type": "token", "token": "answer"}]
        list(_turn(chat_turn_mod, untitled_chat, sample_user_id, events, user_content="find gut studies"))
        assert len(fake_title) == 1

        frames = list(_turn(chat_turn_mod, untitled_chat, sample_user_id, events,
                            user_content="follow-up question"))
        done = _done_payload(frames)
        assert done["title"] == "Gut Microbiome Overview"
        assert len(fake_title) == 1  # no second LLM call

    def test_explicitly_titled_chat_skips_the_llm(self, chat_turn_mod, global_chat_crud,
                                                  sample_user_id, global_chat, fake_title):
        events = [{"type": "agent_start"}, {"type": "token", "token": "answer"}]
        frames = list(_turn(chat_turn_mod, global_chat, sample_user_id, events))
        done = _done_payload(frames)
        assert done["title"] == "durability test"
        assert fake_title == []

    def test_llm_failure_leaves_the_provisional_truncation(self, chat_turn_mod, global_chat_crud,
                                                            sample_user_id, untitled_chat, monkeypatch):
        import helpers.chat_title as title_mod

        def boom(*a, **k):
            raise RuntimeError("nrp down")
        monkeypatch.setattr(title_mod, "llm_chat", boom)

        events = [{"type": "agent_start"}, {"type": "token", "token": "answer"}]
        frames = list(_turn(chat_turn_mod, untitled_chat, sample_user_id, events,
                            user_content="find gut studies"))
        done = _done_payload(frames)
        assert done["title"] == "find gut studies"
        loaded = global_chat_crud.get_global_chat(sample_user_id, untitled_chat)
        assert loaded["title"] == "find gut studies"

    def test_rename_that_lands_mid_turn_wins_over_the_llm_title(self, chat_turn_mod, global_chat_crud,
                                                                sample_user_id, untitled_chat, monkeypatch):
        """The title thread races the turn's own provisional-title write.
        Force a deterministic ordering (provisional write, then rename, then
        the LLM's late persist attempt) with an Event rather than relying on
        real scheduling — otherwise this test would be flaky either way."""
        import helpers.chat_title as title_mod
        provisional_written = threading.Event()

        real_append_user_message = chat_turn_mod.append_user_message

        def wrapped_append_user_message(*a, **k):
            result = real_append_user_message(*a, **k)
            provisional_written.set()
            return result
        chat_turn_mod.append_user_message = wrapped_append_user_message

        def fake_llm_chat(*a, **k):
            assert provisional_written.wait(2.0)
            global_chat_crud.update_global_chat_title(sample_user_id, untitled_chat, "My rename")
            return "LLM title"
        monkeypatch.setattr(title_mod, "llm_chat", fake_llm_chat)

        events = [{"type": "agent_start"}, {"type": "token", "token": "answer"}]
        frames = list(_turn(chat_turn_mod, untitled_chat, sample_user_id, events,
                            user_content="find gut studies"))
        done = _done_payload(frames)
        assert done["title"] == "My rename"
        loaded = global_chat_crud.get_global_chat(sample_user_id, untitled_chat)
        assert loaded["title"] == "My rename"
