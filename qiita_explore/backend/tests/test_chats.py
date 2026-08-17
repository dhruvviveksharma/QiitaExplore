"""Tests for chat functionality."""
import pytest


class TestProjectChats:
    """Test project chat CRUD."""

    def test_create_chat(self, crud, sample_user_id):
        """Create a new chat in a project."""
        project = crud.create_project(sample_user_id, "Chat Project")
        project_id = project["project_id"]

        chat = crud.create_chat(project_id, sample_user_id)
        assert chat is not None
        assert "chat_id" in chat

    def test_list_chats(self, crud, sample_user_id):
        """List all chats in a project."""
        project = crud.create_project(sample_user_id, "Chat Project")
        project_id = project["project_id"]

        crud.create_chat(project_id, sample_user_id)
        crud.create_chat(project_id, sample_user_id)

        chats = crud.list_chats(project_id, sample_user_id)
        assert len(chats) == 2

    def test_delete_chat(self, crud, sample_user_id):
        """Delete a chat."""
        project = crud.create_project(sample_user_id, "Chat Project")
        project_id = project["project_id"]

        chat = crud.create_chat(project_id, sample_user_id)
        chat_id = chat["chat_id"]

        crud.delete_chat(project_id, sample_user_id, chat_id)

        # Verify deleted
        chats = crud.list_chats(project_id, sample_user_id)
        assert len(chats) == 0

    def test_rename_chat(self, crud, sample_user_id):
        project = crud.create_project(sample_user_id, "Chat Project")
        chat = crud.create_chat(project["project_id"], sample_user_id)
        updated = crud.update_chat_title(
            project["project_id"], sample_user_id, chat["chat_id"], "Renamed chat"
        )
        assert updated["title"] == "Renamed chat"
        loaded = crud.get_chat(project["project_id"], sample_user_id, chat["chat_id"])
        assert loaded["title"] == "Renamed chat"

    def test_get_chat_with_messages(self, crud, sample_user_id):
        """Get chat with message history."""
        project = crud.create_project(sample_user_id, "Chat Project")
        project_id = project["project_id"]

        chat = crud.create_chat(project_id, sample_user_id)
        chat_id = chat["chat_id"]

        full_chat = crud.get_chat(project_id, sample_user_id, chat_id)
        assert full_chat is not None
        assert "messages" in full_chat
        assert full_chat["messages"] == []


class TestGlobalChats:
    """Test global chat functionality."""

    def test_create_global_chat(self, global_chat_crud, sample_user_id):
        """Create a new global chat."""
        chat = global_chat_crud.create_global_chat(sample_user_id)
        assert chat is not None
        assert "chat_id" in chat

    def test_list_global_chats(self, global_chat_crud, sample_user_id):
        """List global chats for user."""
        global_chat_crud.create_global_chat(sample_user_id)
        global_chat_crud.create_global_chat(sample_user_id)

        chats = global_chat_crud.list_global_chats(sample_user_id)
        assert len(chats) == 2

    def test_delete_global_chat(self, global_chat_crud, sample_user_id):
        """Delete a global chat."""
        chat = global_chat_crud.create_global_chat(sample_user_id)
        chat_id = chat["chat_id"]

        global_chat_crud.delete_global_chat(sample_user_id, chat_id)

        # Verify deleted
        chats = global_chat_crud.list_global_chats(sample_user_id)
        assert len(chats) == 0

    def test_rename_global_chat(self, global_chat_crud, sample_user_id):
        chat = global_chat_crud.create_global_chat(sample_user_id, title="Old title")
        updated = global_chat_crud.update_global_chat_title(
            sample_user_id, chat["chat_id"], "New title"
        )
        assert updated["title"] == "New title"
        loaded = global_chat_crud.get_global_chat(sample_user_id, chat["chat_id"])
        assert loaded["title"] == "New title"

    def test_global_chats_isolated_by_user(self, global_chat_crud, sample_user_id):
        """Global chats are isolated by user."""
        global_chat_crud.create_global_chat(sample_user_id)
        global_chat_crud.create_global_chat("other_user")

        chats = global_chat_crud.list_global_chats(sample_user_id)
        assert len(chats) == 1


class TestChatMessages:
    """Test appending messages to chats."""

    def test_append_messages(self, crud, sample_user_id):
        """Append user and assistant messages."""
        project = crud.create_project(sample_user_id, "Chat Project")
        project_id = project["project_id"]

        chat = crud.create_chat(project_id, sample_user_id)
        chat_id = chat["chat_id"]

        crud.append_chat_messages(project_id, sample_user_id, chat_id,
                                  "Hello", "Hi there!")

        full_chat = crud.get_chat(project_id, sample_user_id, chat_id)
        assert len(full_chat["messages"]) == 2
        assert full_chat["messages"][0]["role"] == "user"
        assert full_chat["messages"][0]["content"] == "Hello"
        assert full_chat["messages"][1]["role"] == "assistant"
        assert full_chat["messages"][1]["content"] == "Hi there!"

    def test_messages_persist_after_reload(self, crud, sample_user_id):
        """Messages remain after re-fetching chat."""
        project = crud.create_project(sample_user_id, "Chat Project")
        project_id = project["project_id"]

        chat = crud.create_chat(project_id, sample_user_id)
        chat_id = chat["chat_id"]

        crud.append_chat_messages(project_id, sample_user_id, chat_id,
                                  "Test", "Response")

        full_chat = crud.get_chat(project_id, sample_user_id, chat_id)
        assert len(full_chat["messages"]) == 2


class TestPinnedStudiesSurviveReload:
    """Regression: pins vanished on restart because nothing asserted that a
    re-fetched chat still carries them. These are the assertions that would
    have caught it."""

    def test_global_chat_pins_survive_refetch(self, global_chat_crud, sample_user_id):
        import store.cache as cache
        chat_id = global_chat_crud.create_global_chat(sample_user_id)["chat_id"]
        cache.pin_study_to_chat(chat_id, cache.SCOPE_GLOBAL, 11043, "Wild mouse gut")

        # Re-fetch exactly as the frontend does on a cold load.
        reloaded = global_chat_crud.get_global_chat(sample_user_id, chat_id)
        assert reloaded["pinned_studies"] == [11043]
        assert reloaded["pinned_study_meta"] == [
            {"study_id": 11043, "study_title": "Wild mouse gut"}
        ]

    def test_project_chat_pins_survive_refetch(self, crud, sample_user_id, sample_study):
        import store.cache as cache
        project_id = crud.create_project(sample_user_id, "Pin Project")["project_id"]
        crud.add_study_to_project(project_id, sample_user_id, sample_study)
        chat_id = crud.create_chat(project_id, sample_user_id)["chat_id"]
        sid = sample_study["study_id"]
        cache.pin_study_to_chat(chat_id, cache.SCOPE_PROJECT, sid, "Soil survey")

        reloaded = crud.get_chat(project_id, sample_user_id, chat_id)
        assert reloaded["pinned_studies"] == [sid]
        assert reloaded["pinned_study_meta"][0]["study_title"] == "Soil survey"

    def test_unpin_clears_both_views(self, global_chat_crud, sample_user_id):
        import store.cache as cache
        chat_id = global_chat_crud.create_global_chat(sample_user_id)["chat_id"]
        cache.pin_study_to_chat(chat_id, cache.SCOPE_GLOBAL, 11043, "Wild mouse gut")
        cache.unpin_study_from_chat(chat_id, cache.SCOPE_GLOBAL, 11043)

        reloaded = global_chat_crud.get_global_chat(sample_user_id, chat_id)
        assert reloaded["pinned_studies"] == []
        assert reloaded["pinned_study_meta"] == []