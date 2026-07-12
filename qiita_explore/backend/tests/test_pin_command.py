"""Unit tests for /pin store-layer: pin_study_to_chat, unpin, list, scope isolation, cap."""
import pytest


@pytest.fixture
def cache(fresh_db):
    import store.cache as c
    return c


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


def test_scope_isolation(cache):
    cache.pin_study_to_chat(CHAT_A, SCOPE_PROJECT, 11043)
    global_pinned  = cache.list_pinned_studies(CHAT_A, SCOPE_GLOBAL)
    project_pinned = cache.list_pinned_studies(CHAT_A, SCOPE_PROJECT)
    assert 11043 not in global_pinned, "Project-scoped pin leaked into global scope"
    assert 11043 in project_pinned

    cache.pin_study_to_chat(CHAT_A, SCOPE_GLOBAL, 22222)
    project_pinned2 = cache.list_pinned_studies(CHAT_A, SCOPE_PROJECT)
    assert 22222 not in project_pinned2, "Global-scoped pin leaked into project scope"
