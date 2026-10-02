"""helpers/aggregation_tools.py through execute_study_tool, against a real
(temporary) store: this chat's aggregation is created on the first add, adds
union as artifacts, refusals change nothing, and list / save see only the
user's own aggregations."""
import pytest

SID = 10317
# Artifacts: 1, 2 = 16S (preps 7, 8); 5 = Metagenomic (prep 9). s1 in 1 and 5, s2 in 2, s3 in 1.
FILES = {"s1": [("16S", "Raw upload", 1, 2, 0), ("Metagenomic", "Raw upload", 5, 2, 0)],
         "s2": [("16S", "Raw upload", 2, 1, 0)],
         "s3": [("16S", "Raw upload", 1, 2, 0)]}
A2P = {1: 7, 2: 8, 5: 9, 99: 10}          # artifact 99 (prep 10) has no per-sample files


@pytest.fixture
def at(monkeypatch):
    import helpers.study_tools as st
    import helpers.aggregation_tools as at
    monkeypatch.setattr(st, "is_study_public", lambda sid: True)
    monkeypatch.setattr(st, "_fetch_study_header_cached",
                        lambda sid: {"study_id": sid, "study_title": f"Study {sid}", "num_samples": 3})
    monkeypatch.setattr(at, "get_sample_files", lambda sid: FILES)
    monkeypatch.setattr(at, "prep_data_types", lambda sid: {})
    monkeypatch.setattr(at, "artifact_preps", lambda sid: A2P)
    monkeypatch.setattr(at, "list_study_sample_ids", lambda sid: ["s1", "s2", "s3"])
    monkeypatch.setattr(at, "count_fastq_artifacts", lambda sid: 3)
    at.st = st
    return at


def call(at, tool, user="u1", chat="c1", scope="global", **args):
    return at.st.execute_study_tool(tool, args, scope=scope, chat_id=chat, user_id=user)


def chat_agg(user="u1", chat="c1", scope="global"):
    from store import get_chat_aggregation
    return get_chat_aggregation(user, chat, scope)


def test_first_add_creates_this_chats_aggregation(at):
    assert chat_agg() is None
    r = call(at, "add_to_chat_aggregation", study_id=SID, data_types=["16s"])
    a = chat_agg()
    assert a["name"] == "Chat aggregation" and r.ui_payload["aggregation_id"] == a["aggregation_id"]
    st = a["studies"][0]
    assert st["file_filter"]["data_types"] == ["16S"] and st["selected_rows"] == 3       # s1/1, s2/2, s3/1
    p = r.ui_payload
    assert (p["kind"], p["added_rows"], p["totals"]) == ("chat_aggregation_update", 3, {"studies": 1, "rows": 3})
    assert p["undo"] == {"was_new": True, "added_artifacts": None, "prev_artifacts": None, "prev_filter": None}
    assert r.text.startswith("Started this chat's aggregation") and "Undo" in r.text
    from store import list_aggregations
    assert len(list_aggregations("u1")) == 1                         # one temporary aggregation, no saved one


def test_adds_union_as_artifacts_and_record_their_undo(at):
    call(at, "add_to_chat_aggregation", study_id=SID, prep_ids=[7])             # artifact 1
    r = call(at, "add_to_chat_aggregation", study_id=SID, data_types=["Metagenomic"])   # + artifact 5
    st = chat_agg()["studies"][0]
    assert st["file_filter"]["artifacts"] == ["1", "5"] and st["selected_rows"] == 3
    assert r.ui_payload["undo"] == {"was_new": False, "added_artifacts": [5], "prev_artifacts": [1],
                                    "prev_filter": {"data_types": [], "processing": [], "artifacts": ["1"]}}
    r = call(at, "add_to_chat_aggregation", study_id=SID)                       # the rest: artifact 2
    st = chat_agg()["studies"][0]
    assert st["file_filter"] == {"data_types": [], "processing": [], "artifacts": []} and st["selected_rows"] == 4
    assert r.ui_payload["undo"]["added_artifacts"] == [2]


def test_a_union_covering_a_whole_data_type_is_stored_as_that_type(at):
    call(at, "add_to_chat_aggregation", study_id=SID, prep_ids=[7])
    call(at, "add_to_chat_aggregation", study_id=SID, prep_ids=[8])             # artifacts 1 + 2 = all 16S
    assert chat_agg()["studies"][0]["file_filter"]["data_types"] == ["16S"]


@pytest.mark.parametrize("args, why", [
    ({"data_types": ["ITS"]}, "no ITS data"),
    ({"prep_ids": [4242]}, "no prep 4242"),
    ({"prep_ids": [10]}, "per-sample FASTQ/FASTA"),
])
def test_refusals_change_nothing(at, args, why):
    r = call(at, "add_to_chat_aggregation", study_id=SID, **args)
    assert why in r.text and r.ui_payload is None and chat_agg() is None


def test_already_complete_and_already_held(at):
    call(at, "add_to_chat_aggregation", study_id=SID, data_types=["16S"])
    r = call(at, "add_to_chat_aggregation", study_id=SID, prep_ids=[7])
    assert "already holds that part" in r.text and r.ui_payload is None
    call(at, "add_to_chat_aggregation", study_id=SID)
    r = call(at, "add_to_chat_aggregation", study_id=SID, data_types=["Metagenomic"])
    assert "already holds every file" in r.text and r.ui_payload is None


def test_cap_and_project_gate(at, monkeypatch):
    import store.aggregation_crud as crud
    monkeypatch.setattr(at, "AGGREGATION_STUDIES_CAP", 1)
    call(at, "add_to_chat_aggregation", study_id=SID)
    r = call(at, "add_to_chat_aggregation", study_id=1070)
    assert "most it can" in r.text and len(chat_agg()["studies"]) == 1
    monkeypatch.setattr(at.st, "get_project_id_for_chat", lambda chat: "p1")
    monkeypatch.setattr(at.st, "allowed_project_study_ids", lambda pid: {1070})
    r = call(at, "add_to_chat_aggregation", scope="project", study_id=SID)
    assert "not in this project" in r.text and crud is not None


def test_chats_and_users_are_separate(at):
    call(at, "add_to_chat_aggregation", study_id=SID)
    call(at, "add_to_chat_aggregation", chat="c2", study_id=1070)
    call(at, "add_to_chat_aggregation", user="u2", study_id=SID)
    assert [s["study_id"] for s in chat_agg()["studies"]] == [SID]
    assert [s["study_id"] for s in chat_agg(chat="c2")["studies"]] == [1070]
    assert chat_agg(user="u2")["aggregation_id"] != chat_agg()["aggregation_id"]


def test_save_moves_it_to_the_tab(at):
    assert "no aggregation yet" in call(at, "save_chat_aggregation", name="Gut cohort").text
    call(at, "add_to_chat_aggregation", study_id=SID)
    r = call(at, "save_chat_aggregation", name="Gut cohort")
    assert r.ui_payload["kind"] == "aggregation_saved" and r.ui_payload["name"] == "Gut cohort"
    assert chat_agg() is None                                        # the next add starts a new one
    call(at, "add_to_chat_aggregation", study_id=1070)
    assert [s["study_id"] for s in chat_agg()["studies"]] == [1070]


def test_list_shows_this_chats_and_saved_ones_only_for_this_user(at):
    from store import create_aggregation
    call(at, "add_to_chat_aggregation", study_id=SID, data_types=["16S"])
    call(at, "save_chat_aggregation", name="Gut cohort")
    call(at, "add_to_chat_aggregation", study_id=1070)
    create_aggregation("u2", "Someone else's")
    call(at, "add_to_chat_aggregation", chat="other", study_id=SID)    # another chat's temporary one
    r = call(at, "list_aggregations")
    assert r.text.startswith("This chat's aggregation (temporary")
    assert '"Gut cohort" — 1 study, 3 rows checked' in r.text
    assert '10317 "Study 10317": 3 of 3 file rows checked; filter: data type 16S' in r.text
    assert "Someone else's" not in r.text and r.text.count("Chat aggregation") == 1
    p = r.ui_payload
    assert p["kind"] == "aggregation_list" and len(p["aggregation_ids"]) == 1 and p["chat_aggregation_id"]
    r = call(at, "list_aggregations", name="gut")
    assert r.ui_payload["chat_aggregation_id"] is None and "This chat's" not in r.text
    r = call(at, "list_aggregations", name="nope")
    assert r.ui_payload is None and '"Gut cohort"' in r.text
    assert "signed-in" in call(at, "list_aggregations", user=None).text
