"""E2E tests for pinning studies to a chat — both the global-chat and
project-chat scopes, which share the same pin/unpin response shape
(helpers/request_utils.py:pin_response/unpin_response).
"""
import pytest

# store/cache.py:PINNED_STUDIES_PER_CHAT_CAP — kept as a literal here so this
# module stays pure-HTTP (no `store` import / local SQLite bootstrap).
PIN_CAP = 10
INVALID_STUDY_ID = 999999  # guaranteed nonexistent


def _pin(client, base_path, study_id):
    return client.post(f"{base_path}/pinned/{study_id}")


def _unpin(client, base_path, study_id):
    return client.delete(f"{base_path}/pinned/{study_id}")


def _get_chat(client, base_path):
    r = client.get(base_path)
    r.raise_for_status()
    return r.json()


@pytest.mark.e2e
class TestPinningGlobalScope:
    def test_pin_valid_study(self, client, global_chat, public_study_ids):
        base = f"/api/global-chats/{global_chat['chat_id']}"
        study_id = public_study_ids[0]

        r = _pin(client, base, study_id)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert study_id in body["pinned_studies"]

        chat = _get_chat(client, base)
        assert study_id in (chat.get("pinned_studies") or [])

    def test_pin_idempotent(self, client, global_chat, public_study_ids):
        base = f"/api/global-chats/{global_chat['chat_id']}"
        study_id = public_study_ids[0]

        _pin(client, base, study_id)
        _pin(client, base, study_id)

        chat = _get_chat(client, base)
        pinned = chat.get("pinned_studies") or []
        assert pinned.count(study_id) == 1, f"Expected exactly one entry, got: {pinned}"

    def test_pin_invalid_study_rejected(self, client, global_chat):
        base = f"/api/global-chats/{global_chat['chat_id']}"
        r = _pin(client, base, INVALID_STUDY_ID)
        assert r.status_code == 409
        body = r.json()
        assert body["ok"] is False
        assert INVALID_STUDY_ID in body["invalid"]

    def test_unpin_removes_study(self, client, global_chat, public_study_ids):
        base = f"/api/global-chats/{global_chat['chat_id']}"
        study_id = public_study_ids[0]
        _pin(client, base, study_id)

        r = _unpin(client, base, study_id)
        assert r.status_code == 200
        assert study_id not in r.json()["pinned_studies"]

        chat = _get_chat(client, base)
        assert study_id not in (chat.get("pinned_studies") or [])

    def test_pin_cap_enforced(self, client, global_chat, public_study_ids):
        if len(public_study_ids) < PIN_CAP + 1:
            pytest.skip(
                f"need {PIN_CAP + 1} distinct public studies to test the cap, "
                f"only {len(public_study_ids)} available"
            )
        base = f"/api/global-chats/{global_chat['chat_id']}"
        for study_id in public_study_ids[:PIN_CAP]:
            r = _pin(client, base, study_id)
            assert r.status_code == 200, f"Study {study_id} should pin under the cap: {r.text[:200]}"

        overflow_id = public_study_ids[PIN_CAP]
        r = _pin(client, base, overflow_id)
        assert r.status_code == 409
        body = r.json()
        assert overflow_id in body["rejected"], f"Expected {overflow_id} rejected for cap, got: {body}"
        assert len(body["pinned_studies"]) == PIN_CAP


@pytest.mark.e2e
class TestPinningProjectScope:
    def test_pin_valid_study(self, client, project_chat, project, public_study_ids):
        base = f"/api/projects/{project['project_id']}/chats/{project_chat['chat_id']}"
        study_id = public_study_ids[0]

        r = _pin(client, base, study_id)
        assert r.status_code == 200
        assert study_id in r.json()["pinned_studies"]

        chat = _get_chat(client, base)
        assert study_id in (chat.get("pinned_studies") or [])

    def test_pin_invalid_study_rejected(self, client, project_chat, project):
        base = f"/api/projects/{project['project_id']}/chats/{project_chat['chat_id']}"
        r = _pin(client, base, INVALID_STUDY_ID)
        assert r.status_code == 409
        assert INVALID_STUDY_ID in r.json()["invalid"]

    def test_unpin_removes_study(self, client, project_chat, project, public_study_ids):
        base = f"/api/projects/{project['project_id']}/chats/{project_chat['chat_id']}"
        study_id = public_study_ids[0]
        _pin(client, base, study_id)

        r = _unpin(client, base, study_id)
        assert r.status_code == 200
        assert study_id not in r.json()["pinned_studies"]

    def test_pin_against_nonexistent_chat_is_404(self, client, project):
        base = f"/api/projects/{project['project_id']}/chats/does-not-exist"
        r = _pin(client, base, 11043)
        assert r.status_code == 404
