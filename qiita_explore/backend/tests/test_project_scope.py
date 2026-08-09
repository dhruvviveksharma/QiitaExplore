"""Unit tests for project-scoped chat authorization and tools."""
import pytest
from unittest.mock import patch, MagicMock

SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"


@pytest.fixture
def cache(fresh_db):
    import store.cache as c
    return c


@pytest.fixture
def agent_tools(fresh_db):
    import helpers.agent_tools as at
    return at


class TestProjectChatLookup:
    def test_get_project_id_for_chat_found(self, crud, sample_user_id):
        proj = crud.create_project(sample_user_id, "Lookup Test")
        chat = crud.create_chat(proj["project_id"], sample_user_id, "hi")
        assert crud.get_project_id_for_chat(chat["chat_id"]) == proj["project_id"]

    def test_get_project_id_for_chat_missing(self, crud):
        assert crud.get_project_id_for_chat("no-such-chat") is None

    def test_allowed_project_study_ids(self, crud, sample_user_id, sample_study):
        proj = crud.create_project(sample_user_id, "Members")
        crud.add_study_to_project(proj["project_id"], sample_user_id, sample_study)
        ids = crud.allowed_project_study_ids(proj["project_id"])
        assert ids == {sample_study["study_id"]}


class TestPinValidationProjectScope:
    def test_out_of_project_rejected(self, crud, sample_user_id, cache, monkeypatch):
        import helpers.qiita_fetch as qf
        proj = crud.create_project(sample_user_id, "Pin Gate")
        chat = crud.create_chat(proj["project_id"], sample_user_id)
        foreign_id = 99901
        monkeypatch.setattr(qf, "_fetch_study_header_cached", lambda sid: {"study_title": "x", "num_samples": 1})
        pinned_now, invalid, rejected, all_pinned = qf._pin_studies_validated(
            chat["chat_id"], SCOPE_PROJECT, [foreign_id])
        assert foreign_id in invalid
        assert foreign_id not in pinned_now
        assert foreign_id not in all_pinned

    def test_in_project_pinned(self, crud, sample_user_id, sample_study, cache, monkeypatch):
        import helpers.qiita_fetch as qf
        proj = crud.create_project(sample_user_id, "Pin OK")
        crud.add_study_to_project(proj["project_id"], sample_user_id, sample_study)
        chat = crud.create_chat(proj["project_id"], sample_user_id)
        sid = sample_study["study_id"]
        monkeypatch.setattr(qf, "_fetch_study_header_cached",
                            lambda s: {"study_title": "t", "num_samples": 5, "num_preps": 1})
        pinned_now, invalid, rejected, all_pinned = qf._pin_studies_validated(
            chat["chat_id"], SCOPE_PROJECT, [sid])
        assert sid in pinned_now
        assert sid in all_pinned

    def test_global_scope_unchanged(self, cache, monkeypatch):
        import helpers.qiita_fetch as qf
        sid = 11043
        monkeypatch.setattr(qf, "_fetch_study_header_cached",
                            lambda s: {"study_title": "t", "num_samples": 5, "num_preps": 1})
        pinned_now, invalid, rejected, all_pinned = qf._pin_studies_validated(
            "global-chat-1", SCOPE_GLOBAL, [sid])
        assert sid in pinned_now


class TestStalePinRevocation:
    def test_remove_study_purges_pins(self, crud, sample_user_id, sample_study, cache, monkeypatch):
        import helpers.qiita_fetch as qf
        proj = crud.create_project(sample_user_id, "Revoke")
        crud.add_study_to_project(proj["project_id"], sample_user_id, sample_study)
        chat = crud.create_chat(proj["project_id"], sample_user_id)
        sid = sample_study["study_id"]
        monkeypatch.setattr(qf, "_fetch_study_header_cached",
                            lambda s: {"study_title": "t", "num_samples": 5, "num_preps": 1})
        qf._pin_studies_validated(chat["chat_id"], SCOPE_PROJECT, [sid])
        assert sid in cache.list_pinned_studies(chat["chat_id"], SCOPE_PROJECT)

        crud.remove_study_from_project(proj["project_id"], sample_user_id, sid)
        assert sid not in cache.list_pinned_studies(chat["chat_id"], SCOPE_PROJECT)

    def test_stale_pin_hidden_on_read(self, crud, sample_user_id, sample_study, cache):
        """Direct DB insert of a pin for a non-member study must not surface."""
        proj = crud.create_project(sample_user_id, "Stale")
        chat = crud.create_chat(proj["project_id"], sample_user_id)
        sid = sample_study["study_id"]
        cache.pin_study_to_chat(chat["chat_id"], SCOPE_PROJECT, sid, "orphan")
        assert sid not in cache.list_pinned_studies(chat["chat_id"], SCOPE_PROJECT)


class TestProjectTools:
    def test_search_never_hits_postgres(self, agent_tools, crud, sample_user_id, sample_study):
        proj = crud.create_project(sample_user_id, "Local Search")
        crud.add_study_to_project(proj["project_id"], sample_user_id, sample_study)
        with patch("helpers.agent_tools.search_studies_with_sql") as mock_sql:
            result = agent_tools._tool_search_project_studies(
                {"keywords": ["microbiome"]}, project_id=proj["project_id"])
            mock_sql.assert_not_called()
        assert sample_study["study_id"] in {s["study_id"] for s in result.ui_payload["result_studies"]}

    def test_report_rejects_foreign_before_fetch(self, agent_tools, crud, sample_user_id):
        proj = crud.create_project(sample_user_id, "Report Gate")
        with patch("helpers.agent_tools._build_samples_report_payload") as mock_payload:
            result = agent_tools._tool_get_project_study_report(
                {"study_id": 12345}, project_id=proj["project_id"])
            mock_payload.assert_not_called()
        assert result.detail == "not in project"

    def test_execute_tool_rejects_global_search_in_project(self, agent_tools, crud, sample_user_id):
        proj = crud.create_project(sample_user_id, "Reject Global Tool")
        chat = crud.create_chat(proj["project_id"], sample_user_id)
        result = agent_tools.execute_tool(
            "search_studies", {"keywords": ["mouse"]},
            scope=SCOPE_PROJECT, chat_id=chat["chat_id"])
        assert "not available" in result.text

    def test_execute_tool_unknown_scope(self, agent_tools):
        result = agent_tools.execute_tool("pin_study", {"study_ids": [1]},
                                          scope="bogus", chat_id="c")
        assert "Unknown scope" in result.text


class TestAgentDedup:
    def test_search_studies_returns_dedup_flag(self):
        import helpers.agent as agent_mod
        mock_result = MagicMock(text="ok", label="L", detail="D", ui_payload=None)
        with patch.object(agent_mod, "execute_tool", return_value=mock_result):
            events, retval = _collect(agent_mod._execute_tool_call(
                "search_studies", {"keywords": ["a"]}, "id0001",
                scope=SCOPE_GLOBAL, chat_id="c", deep_search=False,
                search_already_done=False))
        assert retval[1] is True

    def test_duplicate_search_skipped_when_already_done(self):
        import helpers.agent as agent_mod
        events, retval = _collect(agent_mod._execute_tool_call(
            "search_project_studies", {"keywords": ["a"]}, "id0002",
            scope=SCOPE_PROJECT, chat_id="c", deep_search=False,
            search_already_done=True))
        assert "Only one" in retval[0]
        assert retval[1] is True


def _collect(gen):
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        return events, stop.value
