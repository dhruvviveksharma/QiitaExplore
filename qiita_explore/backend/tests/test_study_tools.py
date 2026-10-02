"""helpers/study_tools.py and helpers/study_resolve.py: the chat's study tools,
with every Qiita/store read patched on the (freshly imported) module."""
import pytest

from helpers.study_resolve import resolve_text, study_matches, identifying_tokens

SID = 10317
PATH = "/qmounts/qiita_data/"

PREPS = [
    {"prep_template_id": 7, "data_type": "16S", "platform": "Illumina", "target_gene": "16S rRNA",
     "preprocessing_status": "success"},
    {"prep_template_id": 8, "data_type": "Metagenomic", "platform": "Illumina", "preprocessing_status": "success"},
]


def _art(aid, parent=None, prep=None, typ="FASTQ", vis="public", files=2, name=None):
    return {"kind": "artifact", "node_id": f"a{aid}", "parent_node_id": parent, "artifact_id": aid,
            "prep_template_id": prep, "artifact_type": typ, "name": name or typ, "visibility": vis,
            "full_path": f"{PATH}{aid}/x",
            "filepaths": [{"filepath_id": aid * 10 + i, "filename": f"f{aid}_{i}.gz",
                           "filepath_type": "raw_forward_seqs", "full_path": f"{PATH}{aid}/f{aid}_{i}.gz"}
                          for i in range(files)]}


def _job(jid, parent, cmd, params=None):
    return {"kind": "job", "node_id": f"j{jid}", "parent_node_id": parent, "job_id": jid,
            "command_name": cmd, "command_params": params or {}}


GRAPH = [
    _art(1, prep=7, name="raw"),
    _job(1, "a1", "Split libraries FASTQ", {"barcode_type": "golay_12", "reference": f"{PATH}ref.fa"}),
    _art(2, parent="j1", typ="Demultiplexed", files=3, name="demux"),
    _job(2, "a2", "Deblur"),
    _art(3, parent="j2", typ="BIOM", files=1, name="deblur table"),
    _art(4, prep=7, vis="archived", name="old"),
    _art(5, prep=8, name="wgs raw"),
]


@pytest.fixture
def st(monkeypatch):
    import helpers.study_tools as st
    monkeypatch.setattr(st, "is_study_public", lambda sid: sid != 999)
    monkeypatch.setattr(st, "_fetch_study_header_cached",
                        lambda sid: {"study_id": sid, "study_title": "American Gut Project", "num_samples": 4})
    monkeypatch.setattr(st, "load_preps_and_graph", lambda sid: ([dict(p) for p in PREPS], GRAPH))
    monkeypatch.setattr(st, "prep_groups", lambda sid: [{"prep_id": 7, "data_type": "16S", "num_samples": 3},
                                                         {"prep_id": 8, "data_type": "Metagenomic", "num_samples": 2}])
    membership = {"10317.s1": [(7, "16S")], "10317.s2": [(7, "16S"), (8, "Metagenomic")],
                  "10317.s3": [(7, "16S")], "10317.s4": [(8, "Metagenomic")]}
    monkeypatch.setattr(st, "prep_membership", lambda sid: membership)
    monkeypatch.setattr(st, "list_study_sample_ids", lambda sid: sorted(membership))
    monkeypatch.setattr(st, "study_columns", lambda sid: ["sample_type", "host_body_site", "country"])
    monkeypatch.setattr(st, "fetch_prep_samples",
                        lambda sid, pid, limit: ([{"sample_id": s} for s in sorted(membership)
                                                  if any(p == pid for p, _ in membership[s])][:limit],
                                                 sum(any(p == pid for p, _ in v) for v in membership.values())))
    monkeypatch.setattr(st, "fetch_sample_fields",
                        lambda sid, sample: {"country": "USA", "notes": "x" * 500} if sample == "10317.s2" else None)
    return st


def run(st, name, scope="global", **args):
    return st.execute_study_tool(name, args, scope=scope, chat_id="c1")


def no_paths(result):
    import json
    assert PATH not in result.text
    assert "full_path" not in json.dumps(result.ui_payload or {})


# ── gating ──────────────────────────────────────────────────────────────────

def test_private_study_refused_before_any_read(st, monkeypatch):
    monkeypatch.setattr(st, "load_preps_and_graph", lambda sid: pytest.fail("read a private study"))
    r = run(st, "get_study_preps", study_id=999)
    assert "private" in r.text and r.ui_payload is None


def test_project_chat_refuses_out_of_project_study_before_qiita(st, monkeypatch):
    monkeypatch.setattr(st, "get_project_id_for_chat", lambda chat: "p1")
    monkeypatch.setattr(st, "allowed_project_study_ids", lambda pid: {1070})
    monkeypatch.setattr(st, "is_study_public", lambda sid: pytest.fail("touched Qiita"))
    r = run(st, "get_prep_graph", scope="project", study_id=SID)
    assert "not in this project" in r.text and r.ui_payload is None


def test_project_chat_allows_member_study(st, monkeypatch):
    monkeypatch.setattr(st, "get_project_id_for_chat", lambda chat: "p1")
    monkeypatch.setattr(st, "allowed_project_study_ids", lambda pid: {SID})
    assert run(st, "get_study_preps", scope="project", study_id=SID).ui_payload["kind"] == "study_preps"


def test_bad_study_id(st):
    assert run(st, "get_study_preps", study_id="abc").ui_payload is None


# ── get_study_preps ─────────────────────────────────────────────────────────

def test_preps_text_and_payload(st):
    r = run(st, "get_study_preps", study_id=SID)
    assert "16S — 1 preps, 3 samples" in r.text and "Metagenomic — 1 preps, 2 samples" in r.text
    assert "7 · 16S · 3 samples · Illumina · 16S rRNA · success · 3 artifacts" in r.text   # archived a4 not counted
    assert r.ui_payload == {"kind": "study_preps", "study_id": SID, "study_title": "American Gut Project",
                            "data_type": None, "result_summary": "2 preps · 2 data types"}
    no_paths(r)


def test_preps_data_type_filter_case_and_synonym(st):
    assert "Showing the 1 16S preps" in run(st, "get_study_preps", study_id=SID, data_type="16s").text
    r = run(st, "get_study_preps", study_id=SID, data_type="shotgun")
    assert r.ui_payload["data_type"] == "Metagenomic"


def test_preps_unknown_data_type_lists_available_without_widget(st):
    r = run(st, "get_study_preps", study_id=SID, data_type="ITS")
    assert "16S, Metagenomic" in r.text and r.ui_payload is None


# ── get_prep_graph ──────────────────────────────────────────────────────────

def test_graph_outline_hides_archived_and_path_params(st):
    r = run(st, "get_prep_graph", study_id=SID)                       # default: first prep (7)
    assert r.ui_payload["prep_id"] == 7 and r.ui_payload["kind"] == "prep_graph"
    assert "3 artifacts, 2 processing steps (1 archived artifacts hidden" in r.text
    assert 'artifact 2 "demux" (Demultiplexed, public) · 3 files' in r.text
    assert "job Split libraries FASTQ (barcode_type=golay_12)" in r.text   # the path-valued param is dropped
    assert '"old"' not in r.text and "wgs raw" not in r.text
    assert "Other preps: 8" in r.text
    no_paths(r)


def test_graph_accepts_an_artifact_id(st):
    r = run(st, "get_prep_graph", study_id=SID, prep_id=5)
    assert r.ui_payload["prep_id"] == 8 and "5 is an artifact id" in r.text


def test_graph_unknown_prep(st):
    assert run(st, "get_prep_graph", study_id=SID, prep_id=12345).ui_payload is None


def test_graph_text_is_capped(st, monkeypatch):
    big = [_art(1, prep=7)] + [_art(100 + i, parent="a1", name="n" * 90) for i in range(400)]
    monkeypatch.setattr(st, "load_preps_and_graph", lambda sid: (PREPS, big))
    r = run(st, "get_prep_graph", study_id=SID)
    assert len(r.text) <= st._TEXT_MAX and r.text.endswith("(truncated)")
    assert r.text.startswith("Prep 7 (16S) of study 10317") and "401 artifacts" in r.text   # key facts survive


# ── list_artifact_files ─────────────────────────────────────────────────────

def test_files_of_one_artifact_are_filenames_only(st):
    r = run(st, "list_artifact_files", study_id=SID, artifact_id=2)
    assert "f2_0.gz (raw_forward_seqs, file id 20)" in r.text and r.ui_payload["artifact_ids"] == [2]
    no_paths(r)


def test_files_of_a_prep_skip_archived(st):
    r = run(st, "list_artifact_files", study_id=SID, prep_id=7)
    assert r.ui_payload["artifact_ids"] == [1, 2, 3] and r.ui_payload["prep_id"] == 7
    assert r.detail == "6 files · 3 artifacts"
    no_paths(r)


def test_files_artifact_id_that_is_a_prep_id(st):
    assert run(st, "list_artifact_files", study_id=SID, artifact_id=8).ui_payload["artifact_ids"] == [5]


# ── show_study_samples / get_sample_metadata ────────────────────────────────

def test_samples_by_prep(st):
    r = run(st, "show_study_samples", study_id=SID, prep_id=8)
    assert r.ui_payload == {"kind": "study_samples", "study_id": SID, "study_title": "American Gut Project",
                            "prep_id": 8, "data_type": None, "total": 2, "result_summary": "2 samples · prep 8 (Metagenomic)"}
    assert "10317.s2, 10317.s4" in r.text and "sample_type, host_body_site, country" in r.text


def test_samples_by_data_type_and_all(st):
    r = run(st, "show_study_samples", study_id=SID, data_type="16S")
    assert r.ui_payload["total"] == 3 and r.ui_payload["data_type"] == "16S"
    assert run(st, "show_study_samples", study_id=SID).ui_payload["total"] == 4
    assert run(st, "show_study_samples", study_id=SID, data_type="ITS").ui_payload is None


def test_sample_metadata_retries_with_study_prefix_and_clips_values(st):
    r = run(st, "get_sample_metadata", study_id=SID, sample_id="s2")
    assert r.ui_payload["sample_id"] == "10317.s2"
    assert "in prep 7 (16S), prep 8 (Metagenomic)" in r.text and "country: USA" in r.text
    assert "x" * 201 not in r.text and "x" * 200 + "…" in r.text


def test_sample_metadata_not_found(st):
    assert run(st, "get_sample_metadata", study_id=SID, sample_id="nope").ui_payload is None


# ── resolve_study ───────────────────────────────────────────────────────────

AGP = {"study_id": 10317, "study_title": "American Gut Project", "pi_name": "Rob Knight"}
AGP_AU = {"study_id": 11358, "study_title": "American Gut Project Australia", "pi_name": "Someone Else"}
HADZA = {"study_id": 1064, "study_title": "Hadza hunter-gatherer gut microbiome", "pi_name": "Jeff Leach"}


@pytest.mark.parametrize("text, chat, want", [
    ("get me preps related to AGP", [AGP, HADZA], 10317),               # acronym, one pin matches
    ("the Hadza study", [AGP, HADZA], 1064),                             # every identifying word matches
    ("samples from knight's study", [AGP, HADZA], 10317),                # PI surname
    ("show me the samples", [AGP], 10317),                               # names nothing, one pin
    ("gut samples from the pinned study", [AGP], 10317),                 # "pinned", one pin
    ("preps for study 550", [AGP], 550),                                 # explicit id
    ("AGP", [AGP, AGP_AU], None),                                        # two pins match
    ("show me the samples", [AGP, HADZA], None),                         # names nothing, two pins
    ("soil studies from Antarctica", [AGP], None),                       # no pin matches
])
def test_resolve_text(text, chat, want):
    assert resolve_text(text, chat)["study_id"] == want


def test_tokens_drop_command_and_data_type_words():
    assert identifying_tokens("the 16S processing of AGP's samples") == ["agp"]
    assert not study_matches(AGP, ["gut", "soil"])
    assert study_matches(AGP_AU, ["agp"]) and not study_matches(HADZA, ["agp"])


def test_acronym_candidates_find_studies_text_search_misses(st, monkeypatch):
    monkeypatch.setattr(st, "pooled_fetchall", lambda sql, params: [
        (10317, "American Gut Project", None), (11358, "American Gut Project Australia", None),
        (1064, "Hadza hunter-gatherer gut microbiome", None)])
    monkeypatch.setattr(st, "_fetch_study_headers", lambda ids: [{"study_id": i} for i in ids])
    st._titles_cache.clear()
    assert [s["study_id"] for s in st._acronym_candidates("preps related to AGP", 6)] == [10317, 11358]
    assert st._acronym_candidates("preps of 10317", 6) == []


@pytest.fixture
def chat_pins(st, monkeypatch):
    def set_pins(*studies):
        monkeypatch.setattr(st, "list_pinned_studies", lambda chat, scope: [{"study_id": s["study_id"]} for s in studies])
        monkeypatch.setattr(st, "_fetch_study_headers",
                            lambda ids: [s for s in (AGP, AGP_AU, HADZA) if s["study_id"] in ids])
    return set_pins


def test_resolve_tool_uses_the_single_matching_pin(st, chat_pins, monkeypatch):
    chat_pins(AGP, HADZA)
    monkeypatch.setattr(st, "_search_candidates", lambda text, limit: pytest.fail("searched"))
    monkeypatch.setattr(st, "_acronym_candidates", lambda text, limit: pytest.fail("searched"))
    r = run(st, "resolve_study", text="get me preps related to AGP", for_tool="get_study_preps")
    assert r.ui_payload["kind"] == "study_resolved" and r.ui_payload["study_id"] == 10317
    assert "pinned study" in r.text and "Now call get_study_preps with study_id=10317" in r.text


def test_resolve_tool_two_matching_pins_show_a_picker(st, chat_pins, monkeypatch):
    chat_pins(AGP, AGP_AU)
    monkeypatch.setattr(st, "_acronym_candidates", lambda text, limit: [AGP_AU])
    monkeypatch.setattr(st, "_search_candidates", lambda text, limit: [])
    r = run(st, "resolve_study", text="AGP", for_tool="get_prep_graph")
    assert r.ui_payload["kind"] == "study_choice" and r.ui_payload["for_tool"] == "get_prep_graph"
    assert [c["study_id"] for c in r.ui_payload["candidates"]] == [10317, 11358]
    assert all(c["pinned"] for c in r.ui_payload["candidates"]) and "Do not call get_prep_graph" in r.text


def test_resolve_tool_no_pin_match_asks_even_for_a_single_search_hit(st, chat_pins, monkeypatch):
    chat_pins(HADZA)
    monkeypatch.setattr(st, "_acronym_candidates", lambda text, limit: [])
    monkeypatch.setattr(st, "_search_candidates", lambda text, limit: [AGP])
    r = run(st, "resolve_study", text="the AGP preps", for_tool="get_study_preps")
    assert r.ui_payload["kind"] == "study_choice"
    assert [(c["study_id"], c["pinned"]) for c in r.ui_payload["candidates"]] == [(10317, False)]


def test_resolve_tool_explicit_id_must_be_public(st, chat_pins):
    chat_pins()
    assert run(st, "resolve_study", text="study 999").ui_payload is None
    assert run(st, "resolve_study", text="study 1070").ui_payload["study_id"] == 1070


def test_resolve_tool_nothing_found(st, chat_pins, monkeypatch):
    chat_pins()
    monkeypatch.setattr(st, "_acronym_candidates", lambda text, limit: [])
    monkeypatch.setattr(st, "_search_candidates", lambda text, limit: [])
    r = run(st, "resolve_study", text="zebra stripes")
    assert r.ui_payload is None and "Ask the user for the study id" in r.text


def test_resolve_tool_project_chat_offers_only_project_studies(st, monkeypatch):
    monkeypatch.setattr(st, "list_pinned_studies", lambda chat, scope: [])
    monkeypatch.setattr(st, "get_project_id_for_chat", lambda chat: "p1")
    monkeypatch.setattr(st, "get_project_studies_only", lambda pid: {"studies": [AGP, AGP_AU]})
    monkeypatch.setattr(st, "_search_candidates", lambda text, limit: pytest.fail("searched Qiita"))
    monkeypatch.setattr(st, "_acronym_candidates", lambda text, limit: pytest.fail("searched Qiita"))
    r = run(st, "resolve_study", scope="project", text="AGP", for_tool="get_study_preps")
    assert [c["study_id"] for c in r.ui_payload["candidates"]] == [10317, 11358]
    r = run(st, "resolve_study", scope="project", text="hadza", for_tool="get_study_preps")
    assert [c["study_id"] for c in r.ui_payload["candidates"]] == [10317, 11358]   # nothing matched: offer the project
    r = run(st, "resolve_study", scope="project", text="study 1064")
    assert r.ui_payload is None and "not in this project" in r.text


# ── dispatch through agent_tools ────────────────────────────────────────────

@pytest.mark.parametrize("scope", ["global", "project"])
def test_execute_tool_routes_study_tools_in_both_scopes(st, monkeypatch, scope):
    import helpers.agent_tools as at
    seen = []
    monkeypatch.setattr(at, "execute_study_tool", lambda name, args, **kw: seen.append((name, kw)) or "ok")
    assert at.execute_tool("get_prep_graph", {"study_id": SID}, scope=scope, chat_id="c1", user_id=None) == "ok"
    assert seen == [("get_prep_graph", {"scope": scope, "chat_id": "c1", "user_id": None})]


# ── propose_aggregation_add ─────────────────────────────────────────────────

# s1: a 16S file in artifact 1 (prep 7); s2: 16S in artifact 1 and a Metagenomic file in 5 (prep 8).
FILES = {"10317.s1": [("16S", "Raw upload", 1, 2, 0)],
         "10317.s2": [("16S", "Raw upload", 1, 2, 0), ("Metagenomic", "Raw upload", 5, 2, 0)]}


@pytest.fixture
def agg_st(st, monkeypatch):
    monkeypatch.setattr(st, "get_sample_files", lambda sid: FILES)
    monkeypatch.setattr(st, "prep_data_types", lambda sid: {"10317.s3": ["16S"]})
    monkeypatch.setattr(st, "artifact_preps", lambda sid: {1: 7, 2: 7, 3: 7, 5: 8, 9: 9})
    return st


def propose(st, user_id="u1", **args):
    return st.execute_study_tool("propose_aggregation_add", {"study_id": SID, **args},
                                 scope="global", chat_id="c1", user_id=user_id)


def test_proposal_never_writes(agg_st):
    from store import create_aggregation, get_aggregation
    a = create_aggregation("u1", "Gut cohort")
    r = propose(agg_st, data_types=["16S"])
    assert r.ui_payload["suggest"] == {"aggregation_id": a["aggregation_id"], "name": "Gut cohort"}
    assert get_aggregation(a["aggregation_id"], "u1")["studies"] == []
    assert r.text.startswith("Proposal only — nothing was added")


def test_proposal_scopes_by_data_type_and_prep(agg_st):
    r = propose(agg_st, user_id=None, data_types=["16s"])
    assert r.ui_payload["file_filter"] == {"data_types": ["16S"], "processing": [], "artifacts": []}
    assert r.ui_payload["counts"] == {"samples": 2, "rows": 2, "study_rows": 3}
    r = propose(agg_st, user_id=None, prep_ids=[8])
    assert r.ui_payload["file_filter"]["artifacts"] == ["5"] and r.ui_payload["counts"]["rows"] == 1
    assert r.ui_payload["blocked"] is None
    r = propose(agg_st, user_id=None)
    assert r.ui_payload["file_filter"] is None and r.ui_payload["counts"]["rows"] == 3
    no_paths(r)


def test_proposal_blocked_and_unknown(agg_st):
    r = propose(agg_st, user_id=None, prep_ids=[9])        # prep 9's artifact has no per-sample files
    assert r.ui_payload["blocked"] == "prep 9 has no per-sample FASTQ/FASTA files" and "Cannot add" in r.text
    assert propose(agg_st, user_id=None, prep_ids=[4242]).ui_payload is None
    r = propose(agg_st, user_id=None, data_types=["ITS"])
    assert r.ui_payload is None and "16S, Metagenomic" in r.text


def test_proposal_targets_named_new_full_and_containing(agg_st, monkeypatch):
    aggs = [{"aggregation_id": "a1", "name": "Gut cohort", "studies": [{"study_id": SID}]},
            {"aggregation_id": "a2", "name": "Oral", "studies": [{"study_id": i} for i in range(50)]},
            {"aggregation_id": "a3", "name": "Skin", "studies": []},
            {"aggregation_id": "t1", "name": "Chat aggregation", "studies": [], "chat_id": "c1", "chat_scope": "global"}]
    monkeypatch.setattr(agg_st, "list_aggregations", lambda uid: aggs)
    r = propose(agg_st, aggregation_name="gut cohort")
    assert r.ui_payload["suggest"] is None and '"Gut cohort" already has this study' in r.text
    assert '"Oral" already holds 50' in propose(agg_st, aggregation_name="Oral").text
    r = propose(agg_st, aggregation_name="New one")      # new ones start as this chat's aggregation
    assert r.ui_payload["suggest"] is None and "add_to_chat_aggregation" in r.text
    r = propose(agg_st)
    assert r.ui_payload["suggest"] == {"aggregation_id": "a3", "name": "Skin"}   # the only open saved one
    assert "Chat aggregation" not in r.text                                      # temporary ones aren't targets
