"""A turn must not read the transcript it isn't going to use.

Measured on barnacle before this: global_chat_messages held 89,620 chars of
message text and 13,169,472 chars of ui_payload — 147x — because every
samples_report carries up to REPORT_SAMPLE_LIMIT (500) sample rows. Both stream
routes ran build_full_msgs(chat.get('messages'), ...) before the runtime branch,
so a pi-backed turn loaded and json.loads'd the whole thing (12.2 MB for the
largest chat) to build a value pi never receives.

These tests pin the two halves of the fix: the message load is opt-out, and the
history projection never selects ui_payload at all.
"""
import json
import sys
import types

import pytest


def _stub_qiita_core():
    """The route modules import helpers.pg_pool, which imports qiita_core at
    module level — not installed in this sandbox. Fourth copy of this stub
    (test_auth.py, test_internal_tools_scope.py, test_pi_visibility.py); worth
    hoisting into conftest.py as its own change."""
    if "qiita_core" in sys.modules:
        return

    class _FakeTRN:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def add(self, *a, **k): pass
        def execute_fetchindex(self): return []

    fake_sql = types.ModuleType("qiita_db.sql_connection")
    fake_sql.TRN = _FakeTRN()
    fake_db = types.ModuleType("qiita_db")
    fake_db.sql_connection = fake_sql
    sys.modules["qiita_db"] = fake_db
    sys.modules["qiita_db.sql_connection"] = fake_sql

    fake_settings = types.ModuleType("qiita_core.qiita_settings")
    fake_settings.qiita_config = type("_FakeConfig", (), {})()
    fake_core = types.ModuleType("qiita_core")
    fake_core.qiita_settings = fake_settings
    sys.modules["qiita_core"] = fake_core
    sys.modules["qiita_core.qiita_settings"] = fake_settings


_stub_qiita_core()


@pytest.fixture
def chat_with_payload(crud, global_chat_crud):
    """A global chat whose one assistant message carries a fat ui_payload."""
    chat = global_chat_crud.create_global_chat("u1", title="t")
    chat_id = chat["chat_id"]
    payload = {"kind": "samples_report", "study_id": 11043,
               "samples": [{"sample_id": f"s{i}", "fields": {"k": "v" * 40}} for i in range(200)]}
    global_chat_crud.append_global_chat_messages(
        "u1", chat_id, "who are the PIs?", "here you go",
        assistant_ui_payload=payload,
    )
    return chat_id, payload


class TestIncludeMessagesFlag:
    def test_default_still_returns_messages_with_payload_decoded(self, chat_with_payload, global_chat_crud):
        """The hydration endpoints depend on this — it must not regress."""
        chat_id, payload = chat_with_payload
        chat = global_chat_crud.get_global_chat("u1", chat_id)
        assert len(chat["messages"]) == 2
        assistant = chat["messages"][1]
        assert assistant["ui_payload"]["study_id"] == 11043
        assert len(assistant["ui_payload"]["samples"]) == 200, "decoded, not a JSON string"

    def test_opt_out_omits_messages_but_keeps_what_the_route_needs(self, chat_with_payload, global_chat_crud):
        chat_id, _ = chat_with_payload
        chat = global_chat_crud.get_global_chat("u1", chat_id, include_messages=False)
        assert "messages" not in chat
        assert chat["title"] and chat["chat_id"] == chat_id
        assert chat["pinned_studies"] == []

    def test_opt_out_still_returns_none_for_another_users_chat(self, chat_with_payload, global_chat_crud):
        """The ownership check must not be a casualty of the projection change."""
        chat_id, _ = chat_with_payload
        assert global_chat_crud.get_global_chat("someone-else", chat_id, include_messages=False) is None


class TestHistoryProjection:
    def test_history_has_role_and_content_only(self, chat_with_payload, global_chat_crud):
        """ui_payload is the entire cost of the query, so it must not be
        selected and then discarded — it must not be selected."""
        chat_id, _ = chat_with_payload
        rows = global_chat_crud.load_global_chat_history(chat_id)
        assert len(rows) == 2
        for r in rows:
            assert set(r.keys()) == {"role", "content"}

    def test_history_preserves_order_and_roles(self, chat_with_payload, global_chat_crud):
        chat_id, _ = chat_with_payload
        rows = global_chat_crud.load_global_chat_history(chat_id)
        assert [r["role"] for r in rows] == ["user", "assistant"]
        assert rows[0]["content"] == "who are the PIs?"

    def test_build_full_msgs_output_is_unchanged_by_the_projection(self, chat_with_payload, global_chat_crud):
        """The whole change is safe only if the LLM sees the same thing. Compare
        the new projection against the old full load, through build_full_msgs."""
        from helpers.request_utils import build_full_msgs
        chat_id, _ = chat_with_payload
        full = global_chat_crud.get_global_chat("u1", chat_id)["messages"]
        lean = global_chat_crud.load_global_chat_history(chat_id)
        assert build_full_msgs(lean, "next question") == build_full_msgs(full, "next question")


class TestProjectChatListHasNoMessageBodies:
    def test_list_returns_chats_without_messages(self, crud):
        """_load_project_chats used to fan out a message SELECT across every
        chat in the project — 1.68 MB of ui_payload decoded per project open,
        which the frontend never read (hydrateChatCache refetches per chat)."""
        proj = crud.create_project("u1", "p")
        pid = proj["project_id"]
        chat = crud.create_chat(pid, "u1", "t")
        crud.append_chat_messages(pid, "u1", chat["chat_id"], "q", "a",
                                  assistant_ui_payload={"kind": "samples_report", "samples": [1, 2, 3]})
        chats = crud.list_chats(pid, "u1")
        assert chats and "messages" not in chats[0]

    def test_project_detail_still_reports_a_message_count(self, crud):
        proj = crud.create_project("u1", "p")
        pid = proj["project_id"]
        chat = crud.create_chat(pid, "u1", "t")
        crud.append_chat_messages(pid, "u1", chat["chat_id"], "q", "a")
        detail = crud.get_project(pid, "u1")
        listed = [c for c in (detail.get("chats") or []) if c["chat_id"] == chat["chat_id"]]
        assert listed and listed[0]["messages_count"] == 2

    def test_opening_one_chat_still_returns_its_messages(self, crud):
        """The lazy per-chat fetch is what the sidebar change relies on."""
        proj = crud.create_project("u1", "p")
        pid = proj["project_id"]
        chat = crud.create_chat(pid, "u1", "t")
        crud.append_chat_messages(pid, "u1", chat["chat_id"], "q", "a",
                                  assistant_ui_payload={"kind": "samples_report", "study_id": 7})
        opened = crud.get_chat(pid, "u1", chat["chat_id"])
        assert len(opened["messages"]) == 2
        assert opened["messages"][1]["ui_payload"]["study_id"] == 7


class TestStreamRoutesDoNotReadTheTranscript:
    """The regression guard. If build_full_msgs creeps back to the top of a
    stream route, these fail — nothing else would notice, which is how it
    shipped in the first place."""

    def test_global_stream_route_opts_out_of_messages(self):
        import inspect
        from routes import global_chat_routes
        src = inspect.getsource(global_chat_routes.api_global_chat_message_stream)
        assert "include_messages=False" in src
        assert "build_full_msgs(chat" not in src, (
            "the transcript must not be loaded before the runtime branch"
        )

    def test_project_stream_route_opts_out_of_messages(self):
        import inspect
        from routes import chat_routes
        src = inspect.getsource(chat_routes.api_chat_message_stream)
        assert "include_messages=False" in src
        assert "build_full_msgs(chat" not in src

    def test_history_is_loaded_lazily_by_the_paths_that_need_it(self):
        """/pin is the only real consumer — pi owns history/compaction itself
        for the streamed turn. Both routes delegate to the shared
        helpers.chat_turn.stream_chat_turn, which is where that one call lives
        now — the routes themselves never call load_history_for directly."""
        import inspect
        from routes import global_chat_routes, chat_routes
        from helpers import chat_turn
        g = inspect.getsource(global_chat_routes.api_global_chat_message_stream)
        p = inspect.getsource(chat_routes.api_chat_message_stream)
        t = inspect.getsource(chat_turn.stream_chat_turn)
        assert "load_history_for(" not in g
        assert "load_history_for(" not in p
        assert t.count("load_history_for(") == 1, "expected /pin only"


class TestLoadHistoryForDispatch:
    def test_routes_by_scope(self, chat_with_payload, global_chat_crud):
        from helpers.request_utils import load_history_for
        from store.cache import SCOPE_GLOBAL
        chat_id, _ = chat_with_payload
        msgs = load_history_for(chat_id, SCOPE_GLOBAL, "next")
        assert msgs[-1] == {"role": "user", "content": "next"}
        assert [m["role"] for m in msgs[:-1]] == ["user", "assistant"]

    def test_project_scope_reads_the_project_table(self, crud):
        from helpers.request_utils import load_history_for
        from store.cache import SCOPE_PROJECT
        proj = crud.create_project("u1", "p")
        chat = crud.create_chat(proj["project_id"], "u1", "t")
        crud.append_chat_messages(proj["project_id"], "u1", chat["chat_id"], "q", "a")
        msgs = load_history_for(chat["chat_id"], SCOPE_PROJECT, "next")
        assert [m["content"] for m in msgs] == ["q", "a", "next"]
