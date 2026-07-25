"""Integration coverage for the pi-backed project chat wiring in
routes/chat_routes.py — scope token minting, workspace manifest, the
tee()/translate() SSE loop, and persistence.

Mocks only helpers.chat_routes.pi_stream_chat (the one function that talks to
the sidecar over HTTP) — already independently verified in
tests/e2e/test_internal_tools.py-adjacent coverage (Phase 1 sidecar tests +
the cross-language smoke check done while building this). Everything else
here — Flask, SQLite persistence, translate()/build_segments(), SSE framing —
runs for real, fed the same captured real pi event fixture used by
tests/test_pi_translate.py.
"""
import importlib.metadata
import json
import os
import sys
import types
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

_FAKE_QIITA_DB_STUBBED = False


def _stub_qiita_db_and_core():
    global _FAKE_QIITA_DB_STUBBED
    if _FAKE_QIITA_DB_STUBBED:
        return

    class _FakeTRN:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def add(self, *a, **k): pass
        def execute_fetchindex(self): return []

    fake_sql_connection = types.ModuleType("qiita_db.sql_connection")
    fake_sql_connection.TRN = _FakeTRN()
    fake_qiita_db = types.ModuleType("qiita_db")
    fake_qiita_db.sql_connection = fake_sql_connection
    sys.modules["qiita_db"] = fake_qiita_db
    sys.modules["qiita_db.sql_connection"] = fake_sql_connection

    class _FakeConfig:
        pass

    fake_qiita_settings = types.ModuleType("qiita_core.qiita_settings")
    fake_qiita_settings.qiita_config = _FakeConfig()
    fake_qiita_core = types.ModuleType("qiita_core")
    fake_qiita_core.qiita_settings = fake_qiita_settings
    sys.modules["qiita_core"] = fake_qiita_core
    sys.modules["qiita_core.qiita_settings"] = fake_qiita_settings

    import werkzeug
    if not hasattr(werkzeug, "__version__"):
        werkzeug.__version__ = importlib.metadata.version("werkzeug")

    _FAKE_QIITA_DB_STUBBED = True


@pytest.fixture(autouse=True)
def fresh_db():
    return None  # override the parent conftest's per-function autouse fixture


@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("project_chat_pi_test") / "test.db")
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = db_path

    for name in list(sys.modules):
        # helpers.* must be purged too: helpers/auth_middleware.py (and others)
        # do `from store.auth_store import ...` at module level, so a
        # helpers.auth_middleware left over from an earlier test file's own
        # _app fixture stays bound to THAT file's now-orphaned SQLite path —
        # session writes and session reads then silently hit different DBs.
        if (name == "run" or name.startswith("routes.") or name == "store"
                or name.startswith("store.") or name.startswith("helpers.") or "sql_store" in name):
            del sys.modules[name]

    _stub_qiita_db_and_core()
    import run
    import config
    config.PI_SIDECAR_SECRET = "test-pi-secret"
    config.PI_SCOPE_TOKEN_KEY = "test-scope-token-key"
    config.PI_BACKEND_PROJECT = True
    config.PAT_ENCRYPTION_KEY = Fernet.generate_key().decode()
    config.SESSION_COOKIE_SECURE = False
    return run.app


@pytest.fixture
def client(_app):
    return _app.test_client()


@pytest.fixture
def mock_whoami(monkeypatch):
    """Mirrors tests/test_auth.py's fixture of the same name — a controllable
    fake for routes.auth_routes.whoami / helpers.auth_middleware.whoami, keyed
    by token string, with no network call."""
    import routes.auth_routes as auth_routes
    import helpers.auth_middleware as auth_middleware
    from helpers.qiita_client import WhoAmIResult

    responses = {}

    def _fake_whoami(pat):
        return responses.get(pat, WhoAmIResult(ok=False, transient_error=False))

    monkeypatch.setattr(auth_routes, "whoami", _fake_whoami)
    monkeypatch.setattr(auth_middleware, "whoami", _fake_whoami)

    def register(pat, result):
        responses[pat] = result

    register.WhoAmIResult = WhoAmIResult
    return register


def _connect(api_client, mock_whoami, token, principal_idx, **kwargs):
    """Log in a synthetic principal; returns a ready-to-splat headers dict
    carrying the CSRF token these state-changing routes require."""
    result = mock_whoami.WhoAmIResult(ok=True, identity={
        "kind": "human", "principal_idx": principal_idx, "email": "user@test.local",
        "system_role": "user", "scopes": [], "profile_complete": True,
        **kwargs,
    })
    mock_whoami(token, result)
    resp = api_client.post("/api/auth/connect", json={"token": token})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    return {"X-CSRF-Token": body["csrf_token"]}, body["user_id"]


@pytest.fixture
def sample_pi_events():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "pi_events_sample_turn.json")
    with open(path) as f:
        return json.load(f)


def _make_project_with_study(user_id="u1", study_id=11043, title="Wild mice gut microbiome survey"):
    import store.crud as crud
    import store.db as db
    proj = crud.create_project(user_id, "Test workspace")
    project_id = proj["project_id"]
    with db._conn() as conn:
        conn.execute(
            """INSERT INTO project_studies
               (project_id, study_id, study_title, study_abstract, pi_name,
                data_types, num_samples, added_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, study_id, title, "We sampled wild mice gut microbiota.",
             "Dr. Test", "16S, Metagenomic", 50, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
    return project_id


class TestProjectChatPiStream:
    def test_streaming_turn_end_to_end(self, client, mock_whoami, sample_pi_events):
        import store.crud as crud
        headers, user_id = _connect(client, mock_whoami, "qk_pi_stream_1", 401)
        project_id = _make_project_with_study(user_id=user_id)
        chat = crud.create_chat(project_id, user_id, "hi")

        with patch("routes.chat_routes.pi_stream_chat", return_value=iter(sample_pi_events)) as mock_stream:
            resp = client.post(
                f"/api/projects/{project_id}/chats/{chat['chat_id']}/message/stream",
                json={"message": "find studies on wild mice", "model": "qwen3"},
                headers=headers,
            )
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)

        # SSE framing sanity
        assert "event: agent_start" in body
        assert "event: segment_tool_call" in body
        assert "event: segment_tool_result" in body
        assert "event: token" in body
        assert "event: done" in body
        assert "Study 11043" in body  # the assistant's streamed text

        # The sidecar call was made with the right scope/project/system prompt
        call_kwargs = mock_stream.call_args.kwargs
        assert call_kwargs["scope"] == "project"
        assert call_kwargs["chat_id"] == chat["chat_id"]
        assert "workspace" in call_kwargs["context_block"].lower()
        assert "11043" in call_kwargs["context_block"]
        from config import PROJECT_CHAT_AGENT_SYSTEM_PROMPT
        assert call_kwargs["system_prompt"] == PROJECT_CHAT_AGENT_SYSTEM_PROMPT

        # A valid, verifiable scope token was minted for this turn
        from helpers.scope_token import verify_scope_token
        claims = verify_scope_token(call_kwargs["tool_token"])
        assert claims["scope"] == "project"
        assert claims["project_id"] == project_id

        # Persisted correctly: assistant text + agent_segments ui_payload
        persisted = crud.get_chat(project_id, user_id, chat["chat_id"])
        assistant_msg = persisted["messages"][-1]
        assert assistant_msg["role"] == "assistant"
        assert "Study 11043" in assistant_msg["content"]
        assert assistant_msg["ui_payload"]["kind"] == "agent_segments"
        assert assistant_msg["ui_payload"]["segments"][0]["type"] == "tool"

    def test_out_of_workspace_study_mention_does_not_reach_deep_context(self, client, mock_whoami, sample_pi_events):
        """_detect_mentioned_study_ids only returns project-member IDs — a
        stray number in the message that isn't a workspace study must not
        trigger a Postgres-touching deep-context fetch."""
        import store.crud as crud
        headers, user_id = _connect(client, mock_whoami, "qk_pi_stream_2", 402)
        project_id = _make_project_with_study(user_id=user_id)
        chat = crud.create_chat(project_id, user_id, "hi")

        with patch("routes.chat_routes.pi_stream_chat", return_value=iter(sample_pi_events)) as mock_stream:
            resp = client.post(
                f"/api/projects/{project_id}/chats/{chat['chat_id']}/message/stream",
                json={"message": "what about study 10317?", "model": "qwen3"},
                headers=headers,
            )
            assert resp.status_code == 200
            # Flask's SSE generator is lazy — it only runs once the response
            # body is actually consumed, so this must happen INSIDE the patch
            # context or pi_stream_chat's mock never sees the call.
            resp.get_data(as_text=True)

        call_kwargs = mock_stream.call_args.kwargs
        # 10317 is not in this workspace, so it must not appear pulled into
        # the deep-context block (only the workspace manifest should be there).
        assert "PINNED STUDY REPORTS" not in (call_kwargs["context_block"] or "")

    def test_legacy_path_used_when_flag_is_off(self, client, mock_whoami, sample_pi_events, _app):
        import config
        import store.crud as crud
        headers, user_id = _connect(client, mock_whoami, "qk_pi_stream_3", 403)
        project_id = _make_project_with_study(user_id=user_id)
        chat = crud.create_chat(project_id, user_id, "hi")

        config.PI_BACKEND_PROJECT = False
        try:
            with patch("routes.chat_routes.pi_stream_chat") as mock_stream:
                with patch("routes.chat_routes.llm_chat_stream", return_value=iter(["legacy ", "response"])):
                    resp = client.post(
                        f"/api/projects/{project_id}/chats/{chat['chat_id']}/message/stream",
                        json={"message": "hello", "model": "qwen3"},
                        headers=headers,
                    )
                    assert resp.status_code == 200
                    body = resp.get_data(as_text=True)
                    # Tokens are streamed as separate SSE data lines, not
                    # concatenated — check both fragments rather than the
                    # joined string.
                    assert '"token": "legacy '  in body
                    assert '"token": "response"' in body
            mock_stream.assert_not_called()
        finally:
            config.PI_BACKEND_PROJECT = True  # restore for the rest of this module
