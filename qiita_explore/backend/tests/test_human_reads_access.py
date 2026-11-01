"""Qiita's has_human rule, per viewer (helpers/artifact_access.py): the files of a
public artifact that may hold human reads go only to the study's owner, shared
users and Qiita admins — wherever files leave QiitaExplore. Everyone else still
sees the artifact, not its files."""
import importlib.util
import json
import os
import sys
import time

import pytest
from flask import Flask, g

from . import auth_helpers as ah
from .conftest import patch_access, stub_qiita_db_and_core

READER = "reader@test.edu"
LOCKED_NODE = {"files_locked": "human_reads", "filepaths": [], "full_path": None}


def _fresh_module():
    """A private copy with the real functions — conftest stubs the shared ones."""
    spec = importlib.util.find_spec("helpers.artifact_access")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Fetch:
    """pooled_fetchall stand-in: answers in order and records (sql, params)."""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _owner_is(*emails):
    """has_study_rights that grants exactly these emails, recording who asked."""
    asked = []

    def rights(email, study_id):
        asked.append(email)
        return email in emails
    rights.asked = asked
    return rights


# ── the rule ─────────────────────────────────────────────────────────────────

def test_sql_mirrors_qiitas_has_human_and_rights():
    aa = _fresh_module()
    cands = " ".join(aa._CANDIDATES_SQL.split())
    assert "t.artifact_type = 'per_sample_FASTQ'" in cands
    assert "d.data_type NOT IN ('16S', '18S', 'ITS')" in cands
    assert "pt.current_human_filtering IS NOT TRUE" in cands
    assert "LIKE 'human-%%'" in aa._HUMAN_SQL and "qiita.sample_{sid}" in aa._HUMAN_SQL
    rights = " ".join(aa._RIGHTS_SQL.split())
    assert "l.name IN ('admin', 'superuser', 'wet-lab admin')" in rights
    assert "FROM qiita.study WHERE study_id = %s AND lower(email) = %s" in rights
    assert "FROM qiita.study_users WHERE study_id = %s AND lower(email) = %s" in rights


def test_human_read_artifact_ids(monkeypatch):
    aa = _fresh_module()
    fetch = _Fetch([])                                   # no per_sample_FASTQ candidates
    monkeypatch.setattr(aa, "pooled_fetchall", fetch)
    assert aa.human_read_artifact_ids(7) == set() and len(fetch.calls) == 1   # sample_7 never read
    fetch = _Fetch([(10,), (11,)], [(10,)])
    monkeypatch.setattr(aa, "pooled_fetchall", fetch)
    assert aa.human_read_artifact_ids(7) == {10}
    assert "JOIN qiita.sample_7 s" in fetch.calls[1][0] and fetch.calls[1][1] == [[10, 11]]


def test_the_sample_check_is_cached_until_a_new_candidate_appears(monkeypatch):
    aa = _fresh_module()
    fetch = _Fetch([(10,), (11,)], [(10,)],    # first look: candidates, then the sample check
                   [(10,), (11,)],             # again: candidates only, the rest from the cache
                   [(11,)],                    # 10 gone (e.g. archived): still no sample check
                   [(10,), (11,), (12,)], [(10,), (12,)])   # 12 is new: checked again
    monkeypatch.setattr(aa, "pooled_fetchall", fetch)
    assert aa.human_read_artifact_ids(7) == {10}
    assert aa.human_read_artifact_ids(7) == {10} and len(fetch.calls) == 3
    assert aa.human_read_artifact_ids(7) == set() and len(fetch.calls) == 4
    assert aa.human_read_artifact_ids(7) == {10, 12} and len(fetch.calls) == 6
    from store import get_study_human_reads_cache
    assert get_study_human_reads_cache(7) == ([10, 11, 12], [10, 12])


def test_an_unreadable_answer_locks_everything(monkeypatch):
    aa = _fresh_module()
    monkeypatch.setattr(aa, "pooled_fetchall", _Fetch(RuntimeError("db down")))
    locked = aa.human_read_artifact_ids(7)
    assert locked is aa.ALL and 12345 in locked and bool(locked)


def test_has_study_rights(monkeypatch):
    aa = _fresh_module()
    fetch = _Fetch([(True,)], [(False,)], RuntimeError("db down"))
    monkeypatch.setattr(aa, "pooled_fetchall", fetch)
    assert aa.has_study_rights("Owner@X.edu", 7) is True
    assert fetch.calls[0][1] == ["owner@x.edu", 7, "owner@x.edu", 7, "owner@x.edu"]
    assert aa.has_study_rights("someone@x.edu", 7) is False
    assert aa.has_study_rights("someone@x.edu", 7) is False             # a failed read: no rights
    assert aa.has_study_rights(None, 7) is False and len(fetch.calls) == 3   # no viewer: no query


def test_locked_artifact_ids(monkeypatch):
    aa = _fresh_module()
    rights = _owner_is("owner@x.edu")
    monkeypatch.setattr(aa, "has_study_rights", rights)
    monkeypatch.setattr(aa, "human_read_artifact_ids", lambda sid: set())
    assert aa.locked_artifact_ids(7, "x@x.edu") == frozenset() and rights.asked == []
    monkeypatch.setattr(aa, "human_read_artifact_ids", lambda sid: {10})
    assert aa.locked_artifact_ids(7, "owner@x.edu") == frozenset()
    assert aa.locked_artifact_ids(7, "x@x.edu") == {10}
    assert aa.locked_artifact_ids(7, None) == {10}
    monkeypatch.setattr(aa, "human_read_artifact_ids", lambda sid: aa.ALL)
    assert 99 in aa.locked_artifact_ids(7, "x@x.edu")


def test_viewer_is_only_a_signed_in_session():
    aa = _fresh_module()
    assert aa.viewer_email() is None                  # background job / worker thread: no context
    with Flask(__name__).app_context():
        g.user_id, g.session_row = None, {"email": "owner@x.edu"}   # failed re-check: kept for logout only
        assert aa.viewer_email() is None
        g.user_id = "u1"
        assert aa.viewer_email() == "owner@x.edu"


def test_lock_files_takes_out_the_files_only():
    aa = _fresh_module()
    node = {"artifact_id": 10, "name": "raw", "filepaths": [{"filepath_id": 1}], "full_path": "/q/10"}
    assert aa.lock_files(node) == {"artifact_id": 10, "name": "raw", **LOCKED_NODE}
    assert node["filepaths"] and node["full_path"] == "/q/10"            # the cached copy is untouched
    assert aa.lock_files({"artifact_id": 1}) == {"artifact_id": 1, "files_locked": "human_reads"}


# ── the app: /detail, downloads, the manifest ────────────────────────────────

@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = str(tmp_path_factory.mktemp("human_reads") / "test.db")
    for name in list(sys.modules):
        if (name == "run" or name.startswith("routes.") or name == "store" or name.startswith("store.")
                or name.startswith("helpers.") or "sql_store" in name):
            del sys.modules[name]
    stub_qiita_db_and_core()
    import run
    import config
    ah.configure(config)
    return run.app


@pytest.fixture
def client(_app, monkeypatch):
    c = _app.test_client()
    c.csrf = ah.login(c, monkeypatch, READER)
    return c


RAW = {"filepath_id": 100, "filename": "s1.R1.fastq.gz", "full_path": "/q/raw/s1.R1.fastq.gz"}


@pytest.fixture
def detail(client, monkeypatch):
    """Study 5: per_sample_FASTQ 10 (human reads) → job → BIOM 12, both public."""
    import routes.study_routes as sr
    sd = sr.study_detail
    graph = [
        {"kind": "artifact", "node_id": "a10", "parent_node_id": None, "artifact_id": 10, "prep_template_id": 1,
         "artifact_type": "per_sample_FASTQ", "visibility": "public", "full_path": RAW["full_path"],
         "filepaths": [RAW]},
        {"kind": "job", "node_id": "j1", "parent_node_id": "a10"},
        {"kind": "artifact", "node_id": "a12", "parent_node_id": "j1", "artifact_id": 12, "artifact_type": "BIOM",
         "visibility": "public", "full_path": "/q/12.biom",
         "filepaths": [{"filepath_id": 120, "filename": "12.biom", "full_path": "/q/12.biom"}]},
    ]
    monkeypatch.setattr(sr, "is_study_public", lambda sid: True)
    monkeypatch.setattr(sd, "get_study_detail_cache", lambda sid: None)
    monkeypatch.setattr(sd, "upsert_study_detail_cache", lambda *a, **k: None)
    monkeypatch.setattr(sd, "_fetch_study_detail_from_qiita", lambda sid: (
        [{"prep_template_id": 1, "data_type": "Metagenomic"}],
        [{"artifact_id": 10, "full_path": RAW["full_path"]}, {"artifact_id": 12, "full_path": "/q/12.biom"}]))
    monkeypatch.setattr(sd, "fetch_artifact_graph", lambda sid: graph)
    monkeypatch.setattr(sd, "_fetch_prep_metadata_summary", lambda pid: {})
    monkeypatch.setattr(sd, "_fetch_study_samples", lambda sid, limit: ([], 0))
    monkeypatch.setattr(sd, "_fetch_sample_context_text", lambda sid: "")
    monkeypatch.setattr(sd, "prep_membership", lambda sid: {})
    monkeypatch.setattr(sd, "public_artifact_ids", lambda sid: {10, 12})
    monkeypatch.setattr(sd, "public_prep_ids", lambda sid: {1})
    return sd


def test_detail_locks_human_read_files_for_non_owners(client, detail, monkeypatch):
    rights = _owner_is()
    patch_access(monkeypatch, human_read_artifact_ids=lambda sid: {10}, has_study_rights=rights)
    r = client.get("/api/studies/5/detail")
    body = r.get_json()
    nodes = {n["node_id"]: n for n in body["artifact_graph"]}
    assert {k: nodes["a10"][k] for k in LOCKED_NODE} == LOCKED_NODE
    assert nodes["a10"]["artifact_type"] == "per_sample_FASTQ"           # the artifact itself stays
    assert nodes["a12"]["filepaths"][0]["filepath_id"] == 120 and "files_locked" not in nodes["a12"]
    arts = {a["artifact_id"]: a for a in body["artifacts"]}
    assert arts[10]["full_path"] is None and arts[10]["files_locked"] == "human_reads"
    assert "s1.R1" not in json.dumps(body)
    assert rights.asked == [READER]                     # the viewer is the session, nothing in the request
    assert r.headers["Cache-Control"] == "private, no-store"
    # the chat's study tools read through the same filter: outside a request, nobody has rights
    _preps, graph = detail.load_preps_and_graph(5)
    assert next(n for n in graph if n["node_id"] == "a10")["files_locked"] == "human_reads"


def test_detail_gives_owners_their_files(client, detail, monkeypatch):
    patch_access(monkeypatch, human_read_artifact_ids=lambda sid: {10}, has_study_rights=_owner_is(READER))
    body = client.get("/api/studies/5/detail").get_json()
    node = next(n for n in body["artifact_graph"] if n["node_id"] == "a10")
    assert node["filepaths"] == [RAW] and "files_locked" not in node
    assert {a["artifact_id"]: a["full_path"] for a in body["artifacts"]}[10] == RAW["full_path"]


@pytest.fixture
def files(client, monkeypatch, tmp_path):
    """Study 5's public per_sample_FASTQ 10 with one file on disk, as the
    download route's merge_helpers sees it; public_raw_download is on."""
    import routes.artifact_routes as ar
    mh = sys.modules[ar._get_artifacts.__module__]
    p = tmp_path / "s1.R1.fastq.gz"
    p.write_text("reads")
    node = {"kind": "artifact", "artifact_id": 10, "artifact_type": "per_sample_FASTQ", "visibility": "public",
            "paths_v": 2, "full_path": str(p),
            "filepaths": [{"filepath_id": 100, "filename": p.name, "full_path": str(p)}]}
    monkeypatch.setattr(mh, "public_artifact_ids", lambda sid: {10})
    monkeypatch.setattr(mh, "get_study_detail_cache", lambda sid: {
        "artifact_graph_json": json.dumps([node]),
        "artifacts_json": json.dumps([{"artifact_id": 10, "full_path": str(p)}])})
    monkeypatch.setattr(ar, "raw_download_allowed", lambda sid: True)
    return ar, mh


def _download(client):
    return client.get("/api/artifacts/10/files/100/download?study_id=5")


def test_download_of_human_reads_needs_rights(client, files, monkeypatch):
    patch_access(monkeypatch, human_read_artifact_ids=lambda sid: {10}, has_study_rights=_owner_is())
    r = _download(client)
    assert r.status_code == 403 and "human reads" in r.get_json()["error"]
    patch_access(monkeypatch, has_study_rights=_owner_is(READER))
    assert _download(client).status_code == 200


def test_owners_download_raw_data_even_without_public_raw_download(client, files, monkeypatch):
    """Qiita's own rule: a study's raw data is for its owner and shared users
    unless public_raw_download is on."""
    ar, _mh = files
    monkeypatch.setattr(ar, "raw_download_allowed", lambda sid: False)
    patch_access(monkeypatch, has_study_rights=_owner_is())
    assert _download(client).status_code == 403
    patch_access(monkeypatch, has_study_rights=_owner_is(READER))
    assert _download(client).status_code == 200


def test_biom_routes_never_see_locked_artifacts(client, files, monkeypatch):
    _ar, mh = files
    patch_access(monkeypatch, human_read_artifact_ids=lambda sid: {10}, has_study_rights=_owner_is())
    assert mh._get_artifacts(5) == []
    patch_access(monkeypatch, human_read_artifact_ids=lambda sid: set())
    assert [a["artifact_id"] for a in mh._get_artifacts(5)] == [10]


def test_fastq_manifest_needs_rights(client, files, monkeypatch):
    ar, _mh = files
    monkeypatch.setattr(ar, "is_study_public", lambda sid: True)
    monkeypatch.setattr(ar, "fetch_manifest", lambda sid, aid: (1, [("s1", "/q/r1", None)], False))
    monkeypatch.setattr(ar, "to_tsv", lambda rows, paired: "sample-id\tpath\n")
    patch_access(monkeypatch, human_read_artifact_ids=lambda sid: {10}, has_study_rights=_owner_is())
    r = client.get("/api/artifacts/10/fastq-manifest?study_id=5")
    assert r.status_code == 403 and "human reads" in r.get_json()["error"]
    patch_access(monkeypatch, has_study_rights=_owner_is(READER))
    assert client.get("/api/artifacts/10/fastq-manifest?study_id=5").status_code == 200


# ── aggregation file rows and exports ────────────────────────────────────────

ENTRIES = {"s1": [("Metagenomic", "raw", 10, 2, 0), ("16S", "raw", 12, 1, 0)],
           "s2": [("Metagenomic", "raw", 10, 1, 0)]}


@pytest.fixture
def sf(monkeypatch):
    import helpers.sample_files as mod
    mod._memo.clear()
    mod._memo[232] = (time.time(), ENTRIES)
    return mod


def _signed_in(email):
    """An app context with `email` as the signed-in viewer."""
    ctx = Flask(__name__).app_context()
    ctx.push()
    g.user_id, g.session_row = "u1", {"email": email}
    return ctx


def test_aggregation_rows_drop_locked_artifacts_and_keep_the_memo(sf, monkeypatch):
    patch_access(monkeypatch, human_read_artifact_ids=lambda sid: {10}, has_study_rights=_owner_is(READER))
    assert sf.get_sample_files(232) == {"s1": [("16S", "raw", 12, 1, 0)]}       # no viewer: locked
    assert sf._memo[232][1] == ENTRIES                                        # the shared memo keeps all
    ctx = _signed_in(READER)
    try:
        assert sf.get_sample_files(232) == ENTRIES                            # the owner gets everything
    finally:
        ctx.pop()


def test_page_files_never_resolves_locked_artifacts(sf, monkeypatch):
    asked = []
    monkeypatch.setattr(sf, "_study_groups", lambda sids, sel, aids=None: asked.append(aids) or [])
    patch_access(monkeypatch, human_read_artifact_ids=lambda sid: {10}, has_study_rights=_owner_is())
    sf.page_files(232, ["s1", "s2"], ENTRIES, None)
    assert asked == [[12]]


def test_export_drops_locked_groups(monkeypatch):
    import helpers.fastq_manifest as fm
    monkeypatch.setattr(fm, "_BASE", "/q")
    groups = [(232, "Metagenomic", "per_sample_FASTQ", "raw", 10, [], [], set()),
              (232, "16S", "per_sample_FASTQ", "raw", 12, [], [], set())]
    monkeypatch.setattr(fm, "_study_groups", lambda sids, sel, aids=None: groups)
    patch_access(monkeypatch, human_read_artifact_ids=lambda sid: {10}, has_study_rights=_owner_is())
    assert [gr[4] for gr in fm._export_groups({232: {"s1"}}, None)] == [12]
    patch_access(monkeypatch, human_read_artifact_ids=lambda sid: {10, 12})
    with pytest.raises(ValueError, match="human reads"):
        fm._export_groups({232: {"s1"}}, None)


# ── what the model is told ───────────────────────────────────────────────────

def test_list_artifact_files_names_no_locked_file(monkeypatch):
    import helpers.study_tools as st
    locked = {"kind": "artifact", "node_id": "a10", "parent_node_id": None, "artifact_id": 10,
              "prep_template_id": 1, "artifact_type": "per_sample_FASTQ", "visibility": "public",
              "name": "raw", **LOCKED_NODE}
    monkeypatch.setattr(st, "is_study_public", lambda sid: True)
    monkeypatch.setattr(st, "_fetch_study_header_cached", lambda sid: {"study_id": sid, "study_title": "SEPSIS"})
    monkeypatch.setattr(st, "load_preps_and_graph", lambda sid: ([{"prep_template_id": 1}], [locked]))
    r = st.execute_study_tool("list_artifact_files", {"study_id": 5, "artifact_id": 10},
                              scope="global", chat_id="c1")
    assert "artifact 10 \"raw\" (per_sample_FASTQ, public): files restricted" in r.text
    assert "file id" not in r.text and "/q/" not in r.text
    assert "files restricted" in st._node_line(locked)
