"""Lean history reads: the stream routes must never load/decode ui_payload
blobs to build LLM history (measured 12.2MB on a real chat), and the lean
role/content projection must reproduce build_full_msgs' output exactly.
"""
import inspect

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()

from helpers.request_utils import build_full_msgs, load_history_for  # noqa: E402


def _seed_global_chat(global_chat_crud, user_id, n_turns=3):
    chat = global_chat_crud.create_global_chat(user_id, "hist test")
    chat_id = chat["chat_id"]
    for i in range(n_turns):
        global_chat_crud.append_global_chat_messages(
            user_id, chat_id, f"user msg {i}", f"assistant msg {i}",
            assistant_ui_payload={"kind": "agent_segments",
                                  "segments": [{"type": "text", "content": "x" * 500}]},
        )
    return chat_id


class TestIncludeMessagesFlag:

    def test_default_still_returns_messages_with_payload_decoded(self, global_chat_crud, sample_user_id):
        chat_id = _seed_global_chat(global_chat_crud, sample_user_id)
        chat = global_chat_crud.get_global_chat(sample_user_id, chat_id)
        assert len(chat["messages"]) == 6
        assistant_rows = [m for m in chat["messages"] if m["role"] == "assistant"]
        assert assistant_rows[0]["ui_payload"]["kind"] == "agent_segments"

    def test_opt_out_omits_messages_but_keeps_what_the_route_needs(self, global_chat_crud, sample_user_id):
        chat_id = _seed_global_chat(global_chat_crud, sample_user_id)
        chat = global_chat_crud.get_global_chat(sample_user_id, chat_id, include_messages=False)
        assert "messages" not in chat
        assert chat["chat_id"] == chat_id
        assert chat["pinned_studies"] == []

    def test_opt_out_still_returns_none_for_another_users_chat(self, global_chat_crud, sample_user_id):
        chat_id = _seed_global_chat(global_chat_crud, sample_user_id)
        assert global_chat_crud.get_global_chat("someone_else", chat_id,
                                                include_messages=False) is None

    def test_project_scope_opt_out(self, crud, sample_user_id):
        proj = crud.create_project(sample_user_id, "P")
        chat = crud.create_chat(proj["project_id"], sample_user_id, "t")
        crud.append_chat_messages(proj["project_id"], sample_user_id, chat["chat_id"],
                                  "u", "a", assistant_ui_payload={"k": 1})
        lean = crud.get_chat(proj["project_id"], sample_user_id, chat["chat_id"],
                             include_messages=False)
        assert "messages" not in lean
        assert lean["total_studies_in_project"] == 0


class TestHistoryProjection:

    def test_history_has_role_and_content_only(self, global_chat_crud, sample_user_id):
        from store.chat_turn_persist import load_global_chat_history
        chat_id = _seed_global_chat(global_chat_crud, sample_user_id)
        history = load_global_chat_history(chat_id)
        assert all(set(m.keys()) == {"role", "content"} for m in history)

    def test_history_preserves_order_and_roles(self, global_chat_crud, sample_user_id):
        from store.chat_turn_persist import load_global_chat_history
        chat_id = _seed_global_chat(global_chat_crud, sample_user_id, n_turns=2)
        history = load_global_chat_history(chat_id)
        assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
        assert history[0]["content"] == "user msg 0"
        assert history[-1]["content"] == "assistant msg 1"

    def test_build_full_msgs_output_is_unchanged_by_the_projection(self, global_chat_crud, sample_user_id):
        from store.chat_turn_persist import load_global_chat_history
        chat_id = _seed_global_chat(global_chat_crud, sample_user_id)
        full = global_chat_crud.get_global_chat(sample_user_id, chat_id)
        old_way = build_full_msgs(full["messages"], "next question")
        new_way = build_full_msgs(load_global_chat_history(chat_id), "next question")
        assert old_way == new_way

    def test_load_history_for_dispatches_by_scope(self, crud, global_chat_crud, sample_user_id):
        # fresh_db purges store* modules per test; reload request_utils so its
        # module-level loader bindings point at THIS test's temp DB.
        import importlib
        import helpers.request_utils as ru
        ru = importlib.reload(ru)
        from store import SCOPE_GLOBAL, SCOPE_PROJECT
        gchat_id = _seed_global_chat(global_chat_crud, sample_user_id, n_turns=1)
        proj = crud.create_project(sample_user_id, "P2")
        pchat = crud.create_chat(proj["project_id"], sample_user_id, "t")
        crud.append_chat_messages(proj["project_id"], sample_user_id, pchat["chat_id"],
                                  "proj u", "proj a")

        g = ru.load_history_for(gchat_id, SCOPE_GLOBAL, "q")
        p = ru.load_history_for(pchat["chat_id"], SCOPE_PROJECT, "q")
        assert g[0]["content"] == "user msg 0" and g[-1] == {"role": "user", "content": "q"}
        assert p[0]["content"] == "proj u" and p[-1] == {"role": "user", "content": "q"}


class TestStreamRoutesDoNotReadTheTranscript:
    """Source-level guards: the two stream endpoints must use the lean path."""

    def test_global_stream_route_opts_out_of_messages(self):
        import routes.global_chat_routes as m
        src = inspect.getsource(m.api_global_chat_message_stream)
        assert "include_messages=False" in src
        assert "build_full_msgs(chat" not in src

    def test_project_stream_route_opts_out_of_messages(self):
        import routes.chat_routes as m
        src = inspect.getsource(m.api_chat_message_stream)
        assert "include_messages=False" in src
        assert "build_full_msgs(chat" not in src
