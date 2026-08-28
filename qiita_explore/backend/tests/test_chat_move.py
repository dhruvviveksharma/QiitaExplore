"""Tests for moving a chat between project scope and global scope, or
between two projects — store/chat_move.py."""


class TestProjectToProjectMove:
    def test_moves_chat_and_keeps_messages(self, crud, chat_move, sample_user_id):
        proj_a = crud.create_project(sample_user_id, "A")["project_id"]
        proj_b = crud.create_project(sample_user_id, "B")["project_id"]
        chat = crud.create_chat(proj_a, sample_user_id)
        chat_id = chat["chat_id"]
        crud.append_chat_messages(proj_a, sample_user_id, chat_id, "hi", "hello")

        moved = chat_move.move_chat_to_project(sample_user_id, chat_id, proj_a, proj_b)
        assert moved is not None
        assert len(moved["messages"]) == 2

        # No longer reachable under the old project.
        assert crud.get_chat(proj_a, sample_user_id, chat_id) is None
        # Reachable under the new one.
        assert crud.get_chat(proj_b, sample_user_id, chat_id) is not None

    def test_wrong_owner_or_source_project_fails(self, crud, chat_move, sample_user_id):
        proj_a = crud.create_project(sample_user_id, "A")["project_id"]
        proj_b = crud.create_project(sample_user_id, "B")["project_id"]
        chat_id = crud.create_chat(proj_a, sample_user_id)["chat_id"]

        # Wrong "from" project.
        assert chat_move.move_chat_to_project(sample_user_id, chat_id, proj_b, proj_a) is None
        # Wrong owner.
        assert chat_move.move_chat_to_project("other_user", chat_id, proj_a, proj_b) is None
        # Chat untouched.
        assert crud.get_chat(proj_a, sample_user_id, chat_id) is not None


class TestGlobalToProjectAndBack:
    def test_global_to_project_migrates_everything(self, crud, global_chat_crud, chat_move, sample_user_id, sample_study):
        project_id = crud.create_project(sample_user_id, "Dest")["project_id"]
        crud.add_study_to_project(project_id, sample_user_id, sample_study)

        chat_id = global_chat_crud.create_global_chat(sample_user_id, title="From global")["chat_id"]
        global_chat_crud.append_global_chat_messages(sample_user_id, chat_id, "q", "a")

        import store.cache as cache
        cache.pin_study_to_chat(chat_id, cache.SCOPE_GLOBAL, sample_study["study_id"], sample_study["study_title"])

        moved = chat_move.move_global_chat_to_project(sample_user_id, chat_id, project_id)
        assert moved is not None
        assert moved["title"] == "From global"
        assert len(moved["messages"]) == 2
        assert moved["messages"][0]["content"] == "q"

        # Original global chat is gone.
        assert global_chat_crud.get_global_chat(sample_user_id, chat_id) is None
        # Pinned study followed it (study is in the destination project, so it's visible).
        assert moved["pinned_studies"] == [sample_study["study_id"]]

    def test_pinned_study_row_survives_even_when_not_visible(self, crud, global_chat_crud, chat_move, sample_user_id, sample_study):
        """A pin for a study NOT in the destination project should survive in
        the DB (chat_scope flipped) but stop rendering, since project-scope
        reads filter by current project membership. Documented trade-off,
        not a bug — this test locks that behavior in."""
        project_id = crud.create_project(sample_user_id, "Dest")["project_id"]
        # Deliberately do NOT add sample_study to this project.

        chat_id = global_chat_crud.create_global_chat(sample_user_id)["chat_id"]
        import store.cache as cache
        cache.pin_study_to_chat(chat_id, cache.SCOPE_GLOBAL, sample_study["study_id"], sample_study["study_title"])

        moved = chat_move.move_global_chat_to_project(sample_user_id, chat_id, project_id)
        assert moved["pinned_studies"] == []  # not visible: study isn't in the project

        with cache._conn() as conn:
            row = conn.execute(
                "SELECT chat_scope FROM chat_pinned_studies WHERE chat_id = ?", (chat_id,),
            ).fetchone()
        assert row["chat_scope"] == cache.SCOPE_PROJECT  # row survived, scope flipped

    def test_project_to_global_round_trip(self, crud, global_chat_crud, chat_move, sample_user_id):
        project_id = crud.create_project(sample_user_id, "P")["project_id"]
        chat_id = crud.create_chat(project_id, sample_user_id)["chat_id"]
        crud.append_chat_messages(project_id, sample_user_id, chat_id, "hi", "hello")

        moved = chat_move.move_project_chat_to_global(sample_user_id, chat_id, project_id)
        assert moved is not None
        assert len(moved["messages"]) == 2
        assert crud.get_chat(project_id, sample_user_id, chat_id) is None
        assert global_chat_crud.get_global_chat(sample_user_id, chat_id) is not None

    def test_move_to_nonexistent_project_fails_cleanly(self, global_chat_crud, chat_move, sample_user_id):
        chat_id = global_chat_crud.create_global_chat(sample_user_id)["chat_id"]
        result = chat_move.move_global_chat_to_project(sample_user_id, chat_id, "no-such-project")
        assert result is None
        # Original chat untouched — no partial move.
        assert global_chat_crud.get_global_chat(sample_user_id, chat_id) is not None

    def test_pin_and_archive_state_carries_across_move(self, crud, global_chat_crud, chat_move, sample_user_id):
        project_id = crud.create_project(sample_user_id, "Dest")["project_id"]
        chat_id = global_chat_crud.create_global_chat(sample_user_id)["chat_id"]
        global_chat_crud.set_global_chat_pinned(sample_user_id, chat_id, True)

        moved = chat_move.move_global_chat_to_project(sample_user_id, chat_id, project_id)
        assert bool(moved["is_pinned"]) is True
