"""The context bar's data: chat_turn forwards each context_usage event as SSE,
saves the turn's last one on the chat (done or error), and both chat GETs
return it. A request still too long after compacting ends in a plain message.
"""
import importlib
import json

import pytest

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()

from tests.test_context_fit import VLLM, openai_error  # noqa: E402


@pytest.fixture
def chat_turn_mod(fresh_db):
    import helpers.chat_history as chm
    importlib.reload(chm)
    import helpers.chat_turn as ct
    return importlib.reload(ct)


def _usage(total):
    return {"model": "minimax-m2", "window": 204_800, "total": total, "measured": True,
            "parts": {"system": 1, "tools": 2, "studies": 3, "summary": 0, "conversation": 4, "turn": total - 10}}


def _no_context():
    return None
    yield  # pragma: no cover


def _turn(ct, scope, chat_id, user_id, events, project_id=None, raises=None):
    def fake_stream_agent(*a, **kw):
        yield from events
        if raises:
            raise raises

    ct.stream_agent = fake_stream_agent
    return list(ct.stream_chat_turn(
        scope=scope, chat_id=chat_id, user_id=user_id, project_id=project_id, model="minimax-m2",
        user_content="hi", report_study_id=None, pin_study_ids=None, system_prompt="sp", tools=[],
        full_msgs=[{"role": "user", "content": "hi"}], persist=lambda ac, up=None: None,
        build_context=_no_context))


def _frames(frames, event):
    return [json.loads(f.split("data: ", 1)[1]) for f in frames if f.startswith(f"event: {event}\n")]


def test_forwarded_and_saved_on_a_global_chat(chat_turn_mod, global_chat_crud, sample_user_id):
    chat_id = global_chat_crud.create_global_chat(sample_user_id, "t")["chat_id"]
    assert global_chat_crud.get_global_chat(sample_user_id, chat_id)["context_usage"] is None
    frames = _turn(chat_turn_mod, "global", chat_id, sample_user_id, [
        {"type": "agent_start"}, {"type": "context_usage", "usage": _usage(100)},
        {"type": "token", "token": "ok"}, {"type": "context_usage", "usage": _usage(250)}])
    assert [u["total"] for u in _frames(frames, "context_usage")] == [100, 250]
    assert _frames(frames, "done")
    assert global_chat_crud.get_global_chat(sample_user_id, chat_id)["context_usage"] == _usage(250)


def test_saved_on_a_project_chat(chat_turn_mod, crud, sample_user_id):
    pid = crud.create_project(sample_user_id, "p")["project_id"]
    chat_id = crud.create_chat(pid, sample_user_id)["chat_id"]
    _turn(chat_turn_mod, "project", chat_id, sample_user_id,
          [{"type": "context_usage", "usage": _usage(42)}, {"type": "token", "token": "ok"}], project_id=pid)
    assert crud.get_chat(pid, sample_user_id, chat_id)["context_usage"] == _usage(42)


def test_still_too_long_says_so_and_keeps_the_size(chat_turn_mod, global_chat_crud, sample_user_id):
    chat_id = global_chat_crud.create_global_chat(sample_user_id, "t")["chat_id"]
    frames = _turn(chat_turn_mod, "global", chat_id, sample_user_id,
                   [{"type": "context_usage", "usage": _usage(150_000)}], raises=openai_error(VLLM))
    (err,) = _frames(frames, "error")
    assert err["error"].startswith("This conversation is too long for minimax-m2")
    assert global_chat_crud.get_global_chat(sample_user_id, chat_id)["context_usage"]["total"] == 150_000


def test_an_unreadable_saved_value_reads_as_none(global_chat_crud, sample_user_id, db_conn):
    chat_id = global_chat_crud.create_global_chat(sample_user_id, "t")["chat_id"]
    db_conn.execute("UPDATE global_chats SET context_usage = '{oops' WHERE chat_id = ?", (chat_id,))
    db_conn.commit()
    assert global_chat_crud.get_global_chat(sample_user_id, chat_id)["context_usage"] is None
