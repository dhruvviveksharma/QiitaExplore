"""Unit-tier coverage for the internal tool API and hard project-scope
enforcement (routes/internal_tool_routes.py, helpers/project_scope.py,
helpers/scope_token.py).

Deliberately Postgres-free: every case here either short-circuits before any
Qiita DB call (empty input, invalid token, refused out-of-scope study) or
resolves entirely from the local SQLite project_studies mirror (project
search/ranking never touches Postgres — that's the whole point of scoping
locally rather than re-querying and discarding). The Postgres-dependent
happy paths (fetching a real public study's samples, live search results)
are covered in tests/e2e/test_internal_tools.py and
tests/e2e/test_project_scope.py against a running barnacle backend.

Self-contained module (own Flask app import, own qiita_db/qiita_core stub)
rather than sharing tests/test_auth.py's private _app/api_client fixtures —
mirrors the existing "small local fixture per file" convention
(tests/test_pin_command.py's local `cache` fixture) instead of promoting
those private fixtures to conftest.py.
"""
import importlib.metadata
import os
import sys
import types

import pytest

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
    # Override the parent conftest's per-function autouse fixture, which
    # would repoint QIITA_EXPERIMENT_DB_PATH and purge store.* before every
    # test — fighting this file's module-scoped _app/db. Same override
    # tests/e2e/conftest.py uses for the same reason.
    return None


@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("internal_tools_test") / "test.db")
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
    # config may already be cached (import-time os.getenv) from an earlier
    # test file in the same session — set attributes directly rather than
    # relying on env-var timing. Same pattern tests/test_auth.py uses for
    # config.PAT_ENCRYPTION_KEY.
    import config
    config.PI_SIDECAR_SECRET = "test-pi-secret"
    config.PI_SCOPE_TOKEN_KEY = "test-scope-token-key"
    return run.app


@pytest.fixture
def client(_app):
    return _app.test_client()


@pytest.fixture
def crud_mod():
    import store.crud as crud
    return crud


@pytest.fixture
def db_mod():
    import store.db as db
    return db


def _mint(**kwargs):
    from helpers.scope_token import mint_scope_token
    return mint_scope_token(**kwargs)


def _hdrs(tok):
    """Headers for a well-formed internal tool call. The route requires BOTH
    gates (internal_tool_routes._guard + scope token), so a test that sends
    only one of them is testing the guard, not the tool."""
    return {"X-Pi-Secret": "test-pi-secret", "X-Tool-Token": tok}


# ── GET /api/internal/tools/schemas ─────────────────────────────────────────

class TestSchemaEndpoint:
    def test_wrong_secret_rejected(self, client):
        resp = client.get("/api/internal/tools/schemas", headers={"X-Pi-Secret": "wrong"})
        assert resp.status_code == 401

    def test_missing_secret_rejected(self, client):
        resp = client.get("/api/internal/tools/schemas")
        assert resp.status_code == 401

    def test_returns_exactly_the_four_real_tools(self, client):
        resp = client.get("/api/internal/tools/schemas", headers={"X-Pi-Secret": "test-pi-secret"})
        assert resp.status_code == 200
        names = sorted(t["function"]["name"] for t in resp.get_json()["tools"])
        assert names == ["get_study_report", "pin_study", "search_by_sample", "search_studies"]

    def test_byte_identical_to_agent_tools_TOOL_SCHEMAS(self, client):
        from helpers.agent_tools import TOOL_SCHEMAS
        resp = client.get("/api/internal/tools/schemas", headers={"X-Pi-Secret": "test-pi-secret"})
        assert resp.get_json()["tools"] == TOOL_SCHEMAS


# ── _guard(): shared secret + source-IP allowlist ───────────────────────────

class TestInternalToolGuard:
    """The sidecar runs on a different machine than Flask, so the internal tool
    surface is a real network boundary. Three independent gates: source IP,
    shared secret, scope token. These cover the first two — a valid scope token
    alone must not be enough."""

    def test_valid_token_without_shared_secret_rejected(self, client):
        tok = _mint(user_id="u1", scope="global", chat_id="c1")
        resp = client.post("/api/internal/tools/search_studies",
                            headers={"X-Tool-Token": tok}, json={})
        assert resp.status_code == 401

    def test_valid_token_with_wrong_shared_secret_rejected(self, client):
        tok = _mint(user_id="u1", scope="global", chat_id="c1")
        resp = client.post("/api/internal/tools/search_studies",
                            headers={"X-Pi-Secret": "wrong", "X-Tool-Token": tok}, json={})
        assert resp.status_code == 401

    def test_unset_server_secret_fails_closed(self, client, monkeypatch):
        """An unconfigured PI_SIDECAR_SECRET must deny everything, not allow it."""
        import config
        monkeypatch.setattr(config, "PI_SIDECAR_SECRET", None)
        tok = _mint(user_id="u1", scope="global", chat_id="c1")
        assert client.post("/api/internal/tools/search_studies",
                           headers=_hdrs(tok), json={}).status_code == 401
        assert client.get("/api/internal/tools/schemas",
                          headers={"X-Pi-Secret": "test-pi-secret"}).status_code == 401

    def test_disallowed_source_ip_rejected(self, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "PI_ALLOWED_TOOL_CALLERS", ["10.0.0.7"])
        tok = _mint(user_id="u1", scope="global", chat_id="c1")
        # Flask's test client reports remote_addr as 127.0.0.1, not in the list.
        assert client.post("/api/internal/tools/search_studies",
                           headers=_hdrs(tok), json={}).status_code == 401
        assert client.get("/api/internal/tools/schemas",
                          headers={"X-Pi-Secret": "test-pi-secret"}).status_code == 401

    def test_allowed_source_ip_passes_the_guard(self, client, monkeypatch):
        """Control for the case above — proves the allowlist is matching on the
        address rather than rejecting unconditionally."""
        import config
        monkeypatch.setattr(config, "PI_ALLOWED_TOOL_CALLERS", ["127.0.0.1"])
        resp = client.get("/api/internal/tools/schemas",
                          headers={"X-Pi-Secret": "test-pi-secret"})
        assert resp.status_code == 200

    def test_empty_allowlist_means_no_ip_restriction(self, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "PI_ALLOWED_TOOL_CALLERS", [])
        resp = client.get("/api/internal/tools/schemas",
                          headers={"X-Pi-Secret": "test-pi-secret"})
        assert resp.status_code == 200


# ── scope token validation ──────────────────────────────────────────────────

class TestScopeTokenValidation:
    def test_missing_token_rejected(self, client):
        resp = client.post("/api/internal/tools/search_studies", json={})
        assert resp.status_code == 401

    def test_garbage_token_rejected(self, client):
        resp = client.post("/api/internal/tools/search_studies",
                            headers={"X-Tool-Token": "not-a-real-token"}, json={})
        assert resp.status_code == 401

    def test_tampered_signature_rejected(self, client):
        tok = _mint(user_id="u1", scope="global", chat_id="c1")
        body, _, sig = tok.rpartition(".")
        resp = client.post("/api/internal/tools/search_studies",
                            headers={"X-Tool-Token": f"{body}.tamperedxx"}, json={})
        assert resp.status_code == 401

    def test_expired_token_rejected(self, client):
        tok = _mint(user_id="u1", scope="global", chat_id="c1", ttl_seconds=-1)
        resp = client.post("/api/internal/tools/search_studies",
                            headers=_hdrs(tok), json={})
        assert resp.status_code == 401

    def test_unknown_tool_name_rejected(self, client):
        tok = _mint(user_id="u1", scope="global", chat_id="c1")
        resp = client.post("/api/internal/tools/drop_all_tables",
                            headers=_hdrs(tok), json={})
        assert resp.status_code == 404

    def test_project_scope_without_project_id_raises_at_mint_time(self):
        from helpers.scope_token import ScopeTokenError
        with pytest.raises(ScopeTokenError):
            _mint(user_id="u1", scope="project", chat_id="c1")


# ── global scope: empty-input paths (no Postgres touched) ──────────────────

class TestGlobalScopeNoPostgres:
    def test_search_studies_empty_input(self, client):
        tok = _mint(user_id="u1", scope="global", chat_id="c1")
        resp = client.post("/api/internal/tools/search_studies",
                            headers=_hdrs(tok), json={})
        assert resp.status_code == 200
        body = resp.get_json()
        assert "No keywords provided" in body["text"]
        assert body["ui_payload"]["kind"] == "tool_call"

    def test_search_by_sample_empty_input(self, client):
        tok = _mint(user_id="u1", scope="global", chat_id="c1")
        resp = client.post("/api/internal/tools/search_by_sample",
                            headers=_hdrs(tok), json={})
        assert resp.status_code == 200
        assert "No field filters or keywords" in resp.get_json()["text"]

    def test_pin_study_no_valid_ids(self, client):
        tok = _mint(user_id="u1", scope="global", chat_id="c1")
        resp = client.post("/api/internal/tools/pin_study",
                            headers=_hdrs(tok), json={"study_ids": ["not-an-int"]})
        assert resp.status_code == 200
        assert "No valid study IDs" in resp.get_json()["text"]


# ── project scope: hard boundary, entirely SQLite-local ────────────────────

class TestProjectScopeEnforcement:
    def _make_project_with_study(self, crud_mod, db_mod, user_id="u1", study_id=11043,
                                  title="Wild mice gut microbiome survey"):
        proj = crud_mod.create_project(user_id, "Test workspace")
        project_id = proj["project_id"]
        with db_mod._conn() as conn:
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

    def test_search_scoped_to_workspace_finds_member_study(self, client, crud_mod, db_mod):
        project_id = self._make_project_with_study(crud_mod, db_mod)
        tok = _mint(user_id="u1", scope="project", chat_id="c1", project_id=project_id)
        resp = client.post("/api/internal/tools/search_studies", headers=_hdrs(tok),
                            json={"keywords": ["wild", "mice"]})
        assert resp.status_code == 200
        body = resp.get_json()
        ids = [s["study_id"] for s in body["ui_payload"]["result_studies"]]
        assert ids == [11043]

    def test_out_of_project_study_id_never_pulled(self, client, crud_mod, db_mod):
        """The headline case: today's boundary is a prompt sentence and would
        return the data. Here it must be refused before execute_tool() runs —
        proven by the fact this passes with zero Postgres access available."""
        project_id = self._make_project_with_study(crud_mod, db_mod)
        tok = _mint(user_id="u1", scope="project", chat_id="c1", project_id=project_id)
        resp = client.post("/api/internal/tools/get_study_report", headers=_hdrs(tok),
                            json={"study_id": 10317})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ui_payload"] is None
        assert "not in this project's workspace" in body["text"]

    def test_out_of_project_pin_refused(self, client, crud_mod, db_mod):
        project_id = self._make_project_with_study(crud_mod, db_mod)
        tok = _mint(user_id="u1", scope="project", chat_id="c1", project_id=project_id)
        resp = client.post("/api/internal/tools/pin_study", headers=_hdrs(tok),
                            json={"study_ids": [10317, 99999]})
        assert resp.status_code == 200
        assert "nothing pinned" in resp.get_json()["text"]

    def test_search_never_leaks_outside_workspace(self, client, crud_mod, db_mod):
        """Control-adjacent: a broad query that would match many public
        studies globally must still only ever return workspace members."""
        project_id = self._make_project_with_study(crud_mod, db_mod)
        tok = _mint(user_id="u1", scope="project", chat_id="c1", project_id=project_id)
        resp = client.post("/api/internal/tools/search_studies", headers=_hdrs(tok),
                            json={"keywords": ["gut", "microbiome"]})
        assert resp.status_code == 200
        ids = [s["study_id"] for s in resp.get_json()["ui_payload"]["result_studies"]]
        assert all(i == 11043 for i in ids)

    def test_search_by_sample_scoped_to_empty_workspace(self, client, crud_mod):
        """A project with zero studies must not silently fall through to a
        global (unscoped) sample search."""
        proj = crud_mod.create_project("u1", "Empty workspace")
        tok = _mint(user_id="u1", scope="project", chat_id="c1", project_id=proj["project_id"])
        resp = client.post("/api/internal/tools/search_by_sample", headers=_hdrs(tok),
                            json={"keywords": ["gut"]})
        assert resp.status_code == 200
        assert "no studies" in resp.get_json()["text"].lower()

    def test_scope_token_cannot_be_forged_across_users(self, client, crud_mod, db_mod):
        """get_project_studies_only() itself performs no user_id filtering
        (store/crud.py) — the route must independently re-check ownership."""
        project_id = self._make_project_with_study(crud_mod, db_mod, user_id="owner")
        forged_tok = _mint(user_id="attacker", scope="project", chat_id="c1", project_id=project_id)
        resp = client.post("/api/internal/tools/search_studies", headers=_hdrs(forged_tok),
                            json={"keywords": ["wild"]})
        assert resp.status_code == 404

    def test_data_type_filter_applied_locally(self, client, crud_mod, db_mod):
        project_id = self._make_project_with_study(crud_mod, db_mod)
        tok = _mint(user_id="u1", scope="project", chat_id="c1", project_id=project_id)
        resp = client.post("/api/internal/tools/search_studies", headers=_hdrs(tok),
                            json={"keywords": ["wild"], "data_types": ["Proteomic"]})
        assert resp.status_code == 200
        assert resp.get_json()["ui_payload"]["result_studies"] == []
