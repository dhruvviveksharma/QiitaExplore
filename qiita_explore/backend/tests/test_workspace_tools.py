"""helpers/workspace_tools.py: add_to_workspace / create_workspace through
execute_study_tool, against the real (temp) store with the Qiita reads patched."""
import pytest


@pytest.fixture
def wt(monkeypatch):
    import helpers.workspace_tools as wt
    monkeypatch.setattr(wt, "is_study_public", lambda sid: sid != 999)
    monkeypatch.setattr(wt, "_fetch_study_header_cached",
                        lambda sid: {"study_id": sid, "study_title": f"Study {sid}", "pi_name": "PI"})
    wt.enriched = []
    monkeypatch.setattr(wt, "enrich_study_in_project", lambda pid, sid: wt.enriched.append((pid, sid)))
    return wt


def call(tool, user="u1", chat="c1", scope="global", **args):
    import helpers.study_tools as st
    return st.execute_study_tool(tool, args, scope=scope, chat_id=chat, user_id=user)


def names(user="u1"):
    from store import list_projects
    return sorted(p["name"] for p in list_projects(user))


def studies_of(pid, user="u1"):
    from store import get_project
    return sorted(int(s["study_id"]) for s in get_project(pid, user)["studies"])


# ── add_to_workspace ────────────────────────────────────────────────────────

def test_a_new_name_creates_the_workspace(wt):
    r = call("add_to_workspace", study_ids=[1070, 11546], workspace="Twins IBD")
    p = r.ui_payload
    assert p["kind"] == "workspace_update" and p["created"] is True and p["name"] == "Twins IBD"
    assert [a["study_id"] for a in p["added"]] == [1070, 11546] and p["study_count"] == 2
    assert studies_of(p["project_id"]) == [1070, 11546]
    assert wt.enriched == [(p["project_id"], 1070), (p["project_id"], 11546)]
    assert "(created now)" in r.text and "Undo" in r.text


def test_an_existing_workspace_by_exact_or_partial_name(wt):
    from store import create_project
    gut = create_project("u1", "Gut microbiome")["project_id"]
    create_project("u1", "IBD")
    r = call("add_to_workspace", study_ids=[77], workspace="gut")
    assert r.ui_payload["project_id"] == gut and r.ui_payload["created"] is False
    assert names() == ["Gut microbiome", "IBD"]


def test_a_name_matching_several_is_an_error(wt):
    from store import create_project
    create_project("u1", "IBD adults")
    create_project("u1", "IBD kids")
    r = call("add_to_workspace", study_ids=[77], workspace="ibd")
    assert r.ui_payload is None and '"IBD adults"' in r.text and '"IBD kids"' in r.text
    assert names() == ["IBD adults", "IBD kids"]


def test_skips_already_there_private_and_full(wt, monkeypatch):
    monkeypatch.setattr(wt, "PROJECT_STUDIES_CAP", 2)
    first = call("add_to_workspace", study_ids=[1], workspace="W").ui_payload["project_id"]
    p = call("add_to_workspace", study_ids=[1, 999, 2, 3], workspace="W").ui_payload
    assert [a["study_id"] for a in p["added"]] == [2]
    assert {s["study_id"]: s["reason"] for s in p["skipped"]} == {
        1: "already there", 999: "private or not found", 3: "workspace is full (2 studies)"}
    assert studies_of(first) == [1, 2]


def test_workspace_chat_defaults_to_itself_and_skips_the_membership_gate(wt):
    """The study isn't in the workspace yet — the other study tools would refuse it."""
    from store import create_chat, create_project
    pid = create_project("u1", "Mine")["project_id"]
    chat_id = create_chat(pid, "u1")["chat_id"]
    r = call("add_to_workspace", scope="project", chat=chat_id, study_ids=[5])
    assert r.ui_payload["project_id"] == pid and studies_of(pid) == [5]


def test_no_workspace_named_in_a_global_chat_asks(wt):
    from store import create_project
    create_project("u1", "IBD")
    r = call("add_to_workspace", study_ids=[5])
    assert r.ui_payload is None and '"IBD"' in r.text and "create_workspace" in r.text


def test_refusals_change_nothing(wt):
    assert "signed-in" in call("add_to_workspace", user=None, study_ids=[5], workspace="X").text
    assert "not a Qiita study id" in call("add_to_workspace", study_ids=["abc"], workspace="X").text
    assert "At most 10" in call("add_to_workspace", study_ids=list(range(1, 12)), workspace="X").text
    assert "needs study_ids" in call("add_to_workspace", workspace="X").text
    assert names() == [] and names(user="default") == []


def test_users_are_separate(wt):
    call("add_to_workspace", user="u1", study_ids=[5], workspace="Shared name")
    r = call("add_to_workspace", user="u2", study_ids=[6], workspace="Shared name")
    assert r.ui_payload["created"] is True and names("u1") == names("u2") == ["Shared name"]


# ── create_workspace ────────────────────────────────────────────────────────

def test_create_an_empty_workspace_then_never_a_duplicate(wt):
    r = call("create_workspace", name="Twins IBD")
    assert r.ui_payload["created"] is True and r.ui_payload["added"] == [] and r.label == "Created workspace Twins IBD"
    again = call("create_workspace", name="twins ibd")
    assert again.ui_payload["created"] is False and "already exists" in again.text
    assert names() == ["Twins IBD"]


def test_create_with_studies(wt):
    p = call("create_workspace", name="New one", study_ids=[1070]).ui_payload
    assert p["created"] is True and studies_of(p["project_id"]) == [1070]


def test_create_is_exact_name_only(wt):
    """A partial match is a different workspace when creating."""
    from store import create_project
    create_project("u1", "IBD adults")
    assert call("create_workspace", name="IBD").ui_payload["created"] is True
    assert names() == ["IBD", "IBD adults"]
