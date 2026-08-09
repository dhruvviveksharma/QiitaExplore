"""E2E tests for project chats: creating a chat with a first message runs a
blocking (non-streaming) turn before returning (chat_routes.py:37-51), and
every subsequent streamed turn rebuilds the project's study context —
step_done/build_context carries a "<N> studies" detail matching the
project's study count (chat_routes.py:104-106).
"""
import pytest

from parity_helpers import stream_chat


@pytest.mark.e2e
class TestProjectChatCrud:
    def test_chat_appears_on_project(self, client, project, project_chat):
        r = client.get(f"/api/projects/{project['project_id']}")
        chat_ids = {c["chat_id"] for c in r.json()["chats"]}
        assert project_chat["chat_id"] in chat_ids

    def test_delete_chat_removes_it(self, client, project, run_prefix):
        r = client.post(
            f"/api/projects/{project['project_id']}/chats",
            json={"title": f"{run_prefix} to-delete"},
        )
        chat = r.json()

        r = client.delete(f"/api/projects/{project['project_id']}/chats/{chat['chat_id']}")
        assert r.status_code == 200

        r = client.get(f"/api/projects/{project['project_id']}/chats/{chat['chat_id']}")
        assert r.status_code == 404


@pytest.mark.e2e
class TestProjectChatReportGate:
    def test_report_foreign_study_rejected(self, client, project, project_chat, public_study_ids):
        """`/report` with a study not in the project streams refusal text, no ui event."""
        study_id = public_study_ids[0]
        result = stream_chat(
            client, project_chat["chat_id"],
            f"/report {study_id}",
            report_study_id=study_id,
            project_id=project["project_id"],
        )
        assert result["error"] is None
        assert result["ui_payload"] is None
        assert result["persisted"]
        assert "not part of this project" in result["assistant_text"].lower()


@pytest.mark.e2e
@pytest.mark.e2e_llm
class TestProjectChatMessaging:
    def test_create_with_first_message_persists_both_turns(self, client, project):
        r = client.post(
            f"/api/projects/{project['project_id']}/chats",
            json={"message": "In one sentence, what is a prep template in Qiita?"},
        )
        assert r.status_code == 200
        chat = r.json()
        assert len(chat["messages"]) >= 2, (
            f"Expected the first message to run and persist immediately: {chat['messages']}"
        )
        assert chat["messages"][0]["role"] == "user"
        assert chat["messages"][1]["role"] == "assistant"
        assert chat["messages"][1]["content"]

    def test_followup_builds_context_over_project_studies(
        self, client, project, project_chat, public_study_ids,
    ):
        study_id = public_study_ids[0]
        client.post(
            f"/api/projects/{project['project_id']}/studies",
            json={"study": {"study_id": study_id}},
        )

        result = stream_chat(
            client, project_chat["chat_id"],
            "What studies do I have loaded in this project?",
            project_id=project["project_id"],
        )
        assert result["error"] is None, f"Stream returned an error event: {result['error']}"
        assert result["persisted"]
        assert "build_context" in result["step_done_details"], (
            f"Expected a build_context step, got: {result['step_done_labels']}"
        )
        detail = result["step_done_details"]["build_context"]
        assert detail == "1 studies", (
            f"Expected build_context to report the project's 1 study, got: {detail!r}"
        )
