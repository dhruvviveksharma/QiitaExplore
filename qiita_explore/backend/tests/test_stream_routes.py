"""Route-level SSE integration tests for the two chat-stream endpoints —
the first HTTP-layer coverage of the streaming contract (event ordering,
segment persistence, auth/CSRF guards), driven against fake LLM providers.

Uses the test_auth.py purge-and-reimport app pattern (the autouse fresh_db
fixture alone cannot isolate route-level tests — routes bind store functions
by name at import time; see docs/10-testing.md's TKT-041 note). helpers.* are
purged too, so chat_turn/agent bind to the same store instance as the app.
"""
import json
import os
import sys
import types

import pytest

from .conftest import stub_qiita_db_and_core


@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("stream_routes") / "test.db")
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = db_path
    for name in list(sys.modules):
        if (name == "run" or name.startswith("routes.") or name == "store"
                or name.startswith("store.") or name.startswith("helpers.")
                or "sql_store" in name):
            del sys.modules[name]
    stub_qiita_db_and_core()
    import run
    import config
    config.ALLOWED_ORIGINS = []
    config.SESSION_COOKIE_SECURE = False
    return run.app


@pytest.fixture
def client(_app):
    return _app.test_client()


@pytest.fixture
def logged_in(client, monkeypatch):
    """Real user + session via the connect route with a mocked whoami."""
    import routes.auth_routes as auth_routes
    from helpers.qiita_client import WhoAmIResult

    monkeypatch.setattr(auth_routes, "whoami", lambda pat: WhoAmIResult(ok=True, identity={
        "principal_idx": 90001, "email": "stream@test.local",
        "system_role": "user", "scopes": [], "profile_complete": True,
    }))
    resp = client.post("/api/auth/connect", json={"token": "qk_test"})
    assert resp.status_code == 200, resp.get_json()
    return {"X-CSRF-Token": resp.get_json()["csrf_token"]}


def _tool_result(**kw):
    defaults = dict(text="tool says hi", label="Searched", detail="1 result",
                    ui_payload={"kind": "tool_call", "tool": "search_studies",
                                "result_studies": [{"study_id": 1}]},
                    executed=True)
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


@pytest.fixture
def fake_turn(monkeypatch):
    """Patch the provider + tool boundary inside the (re-imported) helpers.agent
    with a one-search-then-text script."""
    from tests.agent.fakes import FakeOpenAIClient, openai_text_round, openai_tool_call_round
    import helpers.agent as agent_mod

    script = [
        openai_tool_call_round("call_stream_1", "search_studies", '{"keywords": ["ibd"]}'),
        openai_text_round("Here you go."),
    ]
    fake_client = FakeOpenAIClient(script)
    calls = []

    def fake_execute_tool(name, args, *, scope, chat_id, deep_search=False):
        calls.append((name, args, scope, deep_search))
        return _tool_result()

    monkeypatch.setattr(agent_mod, "get_client", lambda model: (fake_client, "nrp"))
    monkeypatch.setattr(agent_mod, "execute_tool", fake_execute_tool)
    fake_client.tool_calls = calls
    return fake_client


def parse_sse(raw_text):
    events = []
    for frame in raw_text.split("\n\n"):
        frame = frame.strip()
        if not frame or frame.startswith(":"):
            continue
        lines = frame.split("\n")
        ev = next((l[len("event: "):] for l in lines if l.startswith("event: ")), None)
        data = next((l[len("data: "):] for l in lines if l.startswith("data: ")), None)
        if ev is not None:
            events.append((ev, json.loads(data) if data else {}))
    return events


def _new_global_chat(client, headers):
    resp = client.post("/api/global-chats", json={}, headers=headers)
    assert resp.status_code in (200, 201), resp.get_json()
    return resp.get_json()["chat_id"]


class TestGlobalStream:

    def test_event_ordering_agent_start_through_done(self, client, logged_in, fake_turn):
        chat_id = _new_global_chat(client, logged_in)
        resp = client.post(f"/api/global-chats/{chat_id}/message/stream",
                           json={"message": "find ibd studies", "model": "minimax-m2"},
                           headers=logged_in)
        assert resp.status_code == 200
        events = parse_sse(resp.get_data(as_text=True))
        names = [e for e, _ in events]
        assert names[0] == "agent_start"
        assert names.index("segment_tool_call") < names.index("segment_tool_result")
        assert names.index("segment_tool_result") < names.index("token")
        assert names[-1] == "done"
        assert "error" not in names

    def test_tokens_concatenate_to_the_reply(self, client, logged_in, fake_turn):
        chat_id = _new_global_chat(client, logged_in)
        resp = client.post(f"/api/global-chats/{chat_id}/message/stream",
                           json={"message": "q", "model": "minimax-m2"}, headers=logged_in)
        events = parse_sse(resp.get_data(as_text=True))
        text = "".join(d["token"] for e, d in events if e == "token")
        assert text == "Here you go."

    def test_persistence_rows_and_segment_shape(self, client, logged_in, fake_turn):
        chat_id = _new_global_chat(client, logged_in)
        resp = client.post(f"/api/global-chats/{chat_id}/message/stream",
                           json={"message": "persist me", "model": "minimax-m2"},
                           headers=logged_in)
        live = parse_sse(resp.get_data(as_text=True))

        hydrated = client.get(f"/api/global-chats/{chat_id}", headers=logged_in).get_json()
        msgs = hydrated["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == "persist me"
        assert msgs[1]["content"] == "Here you go."

        ui = msgs[1]["ui_payload"]
        assert ui["kind"] == "agent_segments"
        tool_segs = [s for s in ui["segments"] if s["type"] == "tool"]
        assert len(tool_segs) == 1
        seg = tool_segs[0]
        live_call = next(d for e, d in live if e == "segment_tool_call")
        live_result = next(d for e, d in live if e == "segment_tool_result")
        assert seg["name"] == live_call["name"]
        assert seg["done"] is True
        assert seg["result"]["label"] == live_result["label"]
        assert seg["result"]["ui_payload"] == live_result["ui_payload"]
        assert ui["segments"][-1] == {"type": "text", "content": "Here you go.", "done": True}

    def test_done_carries_pinned_meta_for_agent_turns(self, client, logged_in, fake_turn):
        chat_id = _new_global_chat(client, logged_in)
        resp = client.post(f"/api/global-chats/{chat_id}/message/stream",
                           json={"message": "q", "model": "minimax-m2"}, headers=logged_in)
        done = dict(parse_sse(resp.get_data(as_text=True)))["done"]
        assert done["persisted"] is True
        assert done["pinned_studies"] == []
        assert done["pinned_study_meta"] == []

    def test_deep_search_flag_reaches_the_tool(self, client, logged_in, fake_turn):
        chat_id = _new_global_chat(client, logged_in)
        resp = client.post(f"/api/global-chats/{chat_id}/message/stream",
                           json={"message": "q", "model": "minimax-m2", "deep_search": True},
                           headers=logged_in)
        resp.get_data()  # drain the stream so the generator actually runs
        name, args, scope, deep = fake_turn.tool_calls[0]
        assert (name, scope, deep) == ("search_studies", "global", True)

    def test_stream_requires_csrf(self, client, logged_in, fake_turn):
        chat_id = _new_global_chat(client, logged_in)
        resp = client.post(f"/api/global-chats/{chat_id}/message/stream",
                           json={"message": "q"})  # session cookie present, no CSRF header
        assert resp.status_code == 403
        assert fake_turn.calls == []  # provider never reached

    def test_stream_requires_auth(self, _app, fake_turn):
        fresh = _app.test_client()  # no cookie jar
        resp = fresh.post("/api/global-chats/whatever/message/stream", json={"message": "q"})
        assert resp.status_code == 401
        assert fake_turn.calls == []


class TestProjectStream:

    def _new_project_chat(self, client, headers):
        proj = client.post("/api/projects", json={"name": "SP"}, headers=headers).get_json()
        chat = client.post(f"/api/projects/{proj['project_id']}/chats", json={},
                           headers=headers).get_json()
        return proj["project_id"], chat["chat_id"]

    def test_event_ordering_and_persistence(self, client, logged_in, fake_turn):
        project_id, chat_id = self._new_project_chat(client, logged_in)
        resp = client.post(f"/api/projects/{project_id}/chats/{chat_id}/message/stream",
                           json={"message": "list studies", "model": "minimax-m2"},
                           headers=logged_in)
        assert resp.status_code == 200
        events = parse_sse(resp.get_data(as_text=True))
        names = [e for e, _ in events]
        # project turns emit build_context steps before the agent starts
        assert "step_start" in names and "step_done" in names
        assert names.index("step_done") < names.index("agent_start")
        assert names[-1] == "done"
        hydrated = client.get(f"/api/projects/{project_id}/chats/{chat_id}",
                              headers=logged_in).get_json()
        assert [m["role"] for m in hydrated["messages"]] == ["user", "assistant"]

    def test_report_guard_refuses_non_member_study(self, client, logged_in, fake_turn):
        project_id, chat_id = self._new_project_chat(client, logged_in)
        resp = client.post(f"/api/projects/{project_id}/chats/{chat_id}/message/stream",
                           json={"message": "/report", "report_study_id": 999},
                           headers=logged_in)
        events = parse_sse(resp.get_data(as_text=True))
        text = "".join(d["token"] for e, d in events if e == "token")
        assert "not part of this project" in text
        assert [e for e, _ in events][-1] == "done"
        assert fake_turn.calls == []  # never reached the LLM
