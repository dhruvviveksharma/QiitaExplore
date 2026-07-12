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

    def test_create_global_chat(self, crud, sample_user_id):
        """Create a new global chat."""
        chat = crud.create_global_chat(sample_user_id)
        assert chat is not None
        assert "chat_id" in chat

    def test_list_global_chats(self, crud, sample_user_id):
        """List global chats for user."""
        crud.create_global_chat(sample_user_id)
        crud.create_global_chat(sample_user_id)

        chats = crud.list_global_chats(sample_user_id)
        assert len(chats) == 2

    def test_delete_global_chat(self, crud, sample_user_id):
        """Delete a global chat."""
        chat = crud.create_global_chat(sample_user_id)
        chat_id = chat["chat_id"]

        crud.delete_global_chat(sample_user_id, chat_id)

        # Verify deleted
        chats = crud.list_global_chats(sample_user_id)
        assert len(chats) == 0

    def test_global_chats_isolated_by_user(self, crud, sample_user_id):
        """Global chats are isolated by user."""
        crud.create_global_chat(sample_user_id)
        crud.create_global_chat("other_user")

        chats = crud.list_global_chats(sample_user_id)
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