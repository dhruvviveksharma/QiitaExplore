"""E2E tests for the global chat creation + messaging journey: create a
chat, send a message, and confirm the round trip persisted (done{persisted:
true} on the stream, then the user+assistant pair appended on GET).
"""
import pytest

from parity_helpers import stream_chat


@pytest.mark.e2e
class TestGlobalChatCrud:
    def test_list_includes_created_chat(self, client, global_chat):
        r = client.get("/api/global-chats")
        ids = {c["chat_id"] for c in r.json()["chats"]}
        assert global_chat["chat_id"] in ids

    def test_delete_chat_removes_it(self, client, run_prefix):
        r = client.post("/api/global-chats", json={"title": f"{run_prefix} to-delete"})
        chat = r.json()

        r = client.delete(f"/api/global-chats/{chat['chat_id']}")
        assert r.status_code == 200

        r = client.get(f"/api/global-chats/{chat['chat_id']}")
        assert r.status_code == 404


@pytest.mark.e2e
@pytest.mark.e2e_llm
class TestGlobalChatMessaging:
    def test_message_round_trips_and_persists(self, client, global_chat):
        message = "In one sentence, what is the Qiita microbiome database?"
        result = stream_chat(client, global_chat["chat_id"], message)

        assert result["error"] is None, f"Stream returned an error event: {result['error']}"
        assert result["persisted"], f"done event never reported persisted:true — {result}"
        assert result["assistant_text"], "Expected a non-empty assistant reply"

        r = client.get(f"/api/global-chats/{global_chat['chat_id']}")
        assert r.status_code == 200
        messages = r.json()["messages"]
        assert len(messages) >= 2
        user_msg, assistant_msg = messages[-2], messages[-1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == message
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == result["assistant_text"]

    def test_multi_turn_history_accumulates(self, client, global_chat):
        stream_chat(client, global_chat["chat_id"], "What is 16S rRNA sequencing?")
        stream_chat(
            client, global_chat["chat_id"],
            "And how does that differ from shotgun metagenomics?",
        )

        r = client.get(f"/api/global-chats/{global_chat['chat_id']}")
        messages = r.json()["messages"]
        assert len(messages) >= 4, f"Expected 2 full turns (4 messages), got {len(messages)}"
