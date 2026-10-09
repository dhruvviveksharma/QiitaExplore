"""A chat's temporary aggregation in the store: created with chat_id/chat_scope,
saved (detached), deleted with its chat or project, moved with its chat — and
never touched through another user's chat."""
import pytest


@pytest.fixture
def s():
    import store
    return store


def _temp(s, user, chat_id, scope):
    return s.create_aggregation(user, "Chat aggregation", chat_id=chat_id, chat_scope=scope)


def test_create_get_and_list_carry_the_chat(s):
    a = _temp(s, "u1", "c1", "global")
    saved = s.create_aggregation("u1", "Gut cohort")
    assert s.get_chat_aggregation("u1", "c1", "global")["aggregation_id"] == a["aggregation_id"]
    assert s.get_chat_aggregation("u1", "c1", "project") is None
    assert s.get_chat_aggregation("u2", "c1", "global") is None
    listed = {x["aggregation_id"]: (x["chat_id"], x["chat_scope"]) for x in s.list_aggregations("u1")}
    assert listed == {a["aggregation_id"]: ("c1", "global"), saved["aggregation_id"]: (None, None)}


def test_save_names_and_detaches(s):
    a = _temp(s, "u1", "c1", "global")
    out = s.save_chat_aggregation(a["aggregation_id"], "u1", "Kept")
    assert (out["name"], out["chat_id"], out["chat_scope"]) == ("Kept", None, None)
    assert s.get_chat_aggregation("u1", "c1", "global") is None
    assert s.save_chat_aggregation(a["aggregation_id"], "u2", "Stolen") is None


def test_deleting_a_global_chat_deletes_its_temporary_aggregation(s):
    chat = s.create_global_chat("u1")
    a = _temp(s, "u1", chat["chat_id"], "global")
    kept = s.create_aggregation("u1", "Saved one")
    s.delete_global_chat("u2", chat["chat_id"])                        # someone else's id: nothing happens
    assert s.get_aggregation(a["aggregation_id"], "u1") is not None
    s.delete_global_chat("u1", chat["chat_id"])
    assert s.get_aggregation(a["aggregation_id"], "u1") is None
    assert s.get_aggregation(kept["aggregation_id"], "u1") is not None


def test_deleting_a_project_chat_or_project_deletes_temporary_aggregations(s):
    proj = s.create_project("u1", "P")
    c1 = s.create_chat(proj["project_id"], "u1")["chat_id"]
    c2 = s.create_chat(proj["project_id"], "u1")["chat_id"]
    a1, a2 = _temp(s, "u1", c1, "project"), _temp(s, "u1", c2, "project")
    s.delete_chat(proj["project_id"], "u1", c1)
    assert s.get_aggregation(a1["aggregation_id"], "u1") is None
    assert s.get_aggregation(a2["aggregation_id"], "u1") is not None
    s.delete_project(proj["project_id"], "u2")                          # not theirs: nothing happens
    assert s.get_aggregation(a2["aggregation_id"], "u1") is not None
    s.delete_project(proj["project_id"], "u1")
    assert s.get_aggregation(a2["aggregation_id"], "u1") is None


def test_moving_a_chat_moves_its_temporary_aggregation(s):
    proj = s.create_project("u1", "P")
    chat = s.create_global_chat("u1")
    a = _temp(s, "u1", chat["chat_id"], "global")
    from store.chat_move import move_global_chat_to_project
    move_global_chat_to_project("u1", chat["chat_id"], proj["project_id"])
    assert s.get_chat_aggregation("u1", chat["chat_id"], "project")["aggregation_id"] == a["aggregation_id"]
    assert s.get_chat_aggregation("u1", chat["chat_id"], "global") is None


def test_remove_rows_by_artifacts(s):
    a = s.create_aggregation("u1", "A")
    aid = a["aggregation_id"]
    s.add_study_to_aggregation(aid, "u1", {"study_id": 7}, 2, rows=[("x", 1), ("y", 1), ("x", 2), ("x", 3)])

    def count():
        return s.get_aggregation(aid, "u1")["studies"][0]["selected_rows"]
    s.remove_rows_by_artifacts(aid, "u1", 7, [2])
    assert count() == 3
    s.remove_rows_by_artifacts(aid, "u1", 7, [1], keep=True)               # keep only artifact 1
    assert count() == 2
    assert s.remove_rows_by_artifacts(aid, "u2", 7, [1]) is None
