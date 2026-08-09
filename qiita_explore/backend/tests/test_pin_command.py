"""Unit tests for /pin store-layer: pin_study_to_chat, unpin, list, scope isolation, cap."""
import pytest


@pytest.fixture
def cache(fresh_db):
    import store.cache as c
    return c


@pytest.fixture
def request_utils(fresh_db):
    import helpers.request_utils as ru
    return ru


SCOPE_PROJECT = "project"
SCOPE_GLOBAL  = "global"
CHAT_A = "chat-unit-001"


def test_pin_stores_and_retrieves(cache):
    result = cache.pin_study_to_chat(CHAT_A, SCOPE_GLOBAL, 11043)
    assert result is True
    pinned = cache.list_pinned_studies(CHAT_A, SCOPE_GLOBAL)
    assert pinned == [11043]


def test_pin_idempotent(cache):
    assert cache.pin_study_to_chat(CHAT_A, SCOPE_GLOBAL, 11043) is True
    assert cache.pin_study_to_chat(CHAT_A, SCOPE_GLOBAL, 11043) is True
    pinned = cache.list_pinned_studies(CHAT_A, SCOPE_GLOBAL)
    assert pinned.count(11043) == 1, "Duplicate row written for same study"


def test_pin_cap_enforced(cache):
    for i in range(cache.PINNED_STUDIES_PER_CHAT_CAP):
        ok = cache.pin_study_to_chat(CHAT_A, SCOPE_GLOBAL, 10000 + i)
        assert ok is True, f"Study {10000 + i} should pin successfully (slot {i+1})"
    over_cap = cache.pin_study_to_chat(CHAT_A, SCOPE_GLOBAL, 99999)
    assert over_cap is False, "Expected False when cap is reached"
    pinned = cache.list_pinned_studies(CHAT_A, SCOPE_GLOBAL)
    assert 99999 not in pinned


def test_unpin_removes_study(cache):
    cache.pin_study_to_chat(CHAT_A, SCOPE_GLOBAL, 11043)
    cache.unpin_study_from_chat(CHAT_A, SCOPE_GLOBAL, 11043)
    pinned = cache.list_pinned_studies(CHAT_A, SCOPE_GLOBAL)
    assert 11043 not in pinned


def test_pin_stores_title_for_the_chip(cache):
    cache.pin_study_to_chat(CHAT_A, SCOPE_GLOBAL, 11043, "Gut microbiome of wild mice")
    meta = cache.list_pinned_study_meta(CHAT_A, SCOPE_GLOBAL)
    assert meta == [{"study_id": 11043, "study_title": "Gut microbiome of wild mice"}]


def test_pin_without_title_falls_back_to_null(cache):
    """Rows pinned before study_title existed must still load — the chip falls
    back to the bare study ID rather than the whole list failing to hydrate."""
    cache.pin_study_to_chat(CHAT_A, SCOPE_GLOBAL, 11043)
    meta = cache.list_pinned_study_meta(CHAT_A, SCOPE_GLOBAL)
    assert meta == [{"study_id": 11043, "study_title": None}]


class TestPinResponseSurfacesRejection:
    """A pin that doesn't land must not answer 200 {'ok': True} — that silence
    is what let failed pins look successful until the next reload."""

    def _ctx(self):
        from flask import Flask
        return Flask(__name__).test_request_context()

    def test_rejected_study_returns_409(self, request_utils, monkeypatch):
        # study 999999 comes back as invalid (private / not found / lookup failed)
        monkeypatch.setattr(request_utils, "_pin_studies_validated",
                            lambda *a, **k: ([], [999999], [], []))
        with self._ctx():
            resp, status = request_utils.pin_response("chat-1", SCOPE_GLOBAL, 999999)
        assert status == 409
        body = resp.get_json()
        assert body["ok"] is False
        assert body["invalid"] == [999999]
        assert "999999" in body["error"]

    def test_over_cap_returns_409_with_cap_message(self, request_utils, monkeypatch):
        monkeypatch.setattr(request_utils, "_pin_studies_validated",
                            lambda *a, **k: ([], [], [777], []))
        with self._ctx():
            resp, status = request_utils.pin_response("chat-1", SCOPE_GLOBAL, 777)
        assert status == 409
        assert "limit" in resp.get_json()["error"].lower()

    def test_successful_pin_returns_200(self, request_utils, monkeypatch):
        """Response contract only. The meta round-trip through SQLite is covered
        by test_pin_stores_title_for_the_chip and TestPinnedStudiesSurviveReload —
        fresh_db purges store.* per test, so request_utils' bound import points at
        a different module instance than a store fixture would."""
        monkeypatch.setattr(request_utils, "_pin_studies_validated",
                            lambda *a, **k: ([11043], [], [], [11043]))
        monkeypatch.setattr(request_utils, "list_pinned_study_meta",
                            lambda *a, **k: [{"study_id": 11043, "study_title": "Wild mouse gut"}])
        with self._ctx():
            resp = request_utils.pin_response("chat-1", SCOPE_GLOBAL, 11043)
        # a bare 200 response, not a (body, status) tuple
        body = resp.get_json()
        assert body["ok"] is True
        assert body["pinned_study_meta"] == [{"study_id": 11043, "study_title": "Wild mouse gut"}]
        # Derived from the meta, not a second query over the same rows.
        assert body["pinned_studies"] == [11043]


def test_scope_isolation(cache, crud, sample_user_id, sample_study):
    proj = crud.create_project(sample_user_id, "Scope Iso")
    crud.add_study_to_project(proj["project_id"], sample_user_id, sample_study)
    chat = crud.create_chat(proj["project_id"], sample_user_id)
    chat_id = chat["chat_id"]
    sid = sample_study["study_id"]

    cache.pin_study_to_chat(chat_id, SCOPE_PROJECT, sid)
    global_pinned  = cache.list_pinned_studies(chat_id, SCOPE_GLOBAL)
    project_pinned = cache.list_pinned_studies(chat_id, SCOPE_PROJECT)
    assert sid not in global_pinned, "Project-scoped pin leaked into global scope"
    assert sid in project_pinned

    cache.pin_study_to_chat(chat_id, SCOPE_GLOBAL, 22222)
    project_pinned2 = cache.list_pinned_studies(chat_id, SCOPE_PROJECT)
    assert 22222 not in project_pinned2, "Global-scoped pin leaked into project scope"
