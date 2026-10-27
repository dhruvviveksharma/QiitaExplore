"""TKT-043: artifact files, sample lists, sample counts and merge workspaces
reach only public artifacts of public studies — login is open to every
verified Qiita user, so being signed in is not permission to read private data."""
import json
import os
import sys

import pytest

from . import auth_helpers as ah
from .conftest import stub_qiita_db_and_core


@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = str(tmp_path_factory.mktemp("artifact_access") / "test.db")
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
    c.csrf = ah.login(c, monkeypatch, "reader@test.edu")
    return c


@pytest.fixture
def qiita(monkeypatch, tmp_path):
    """Study 1 is public and holds a public artifact (10) and a private one (11);
    study 2 is private. Both artifacts' files exist on disk. Patched on the
    helpers module the running app imported."""
    import routes.artifact_routes as ar
    mh = sys.modules[ar._get_artifacts.__module__]
    files = {}
    for aid in (10, 11):
        p = tmp_path / f"art{aid}.biom"
        p.write_text(f"artifact {aid}")
        files[aid] = str(p)
    graph = [{"kind": "artifact", "artifact_id": aid, "visibility": vis, "paths_v": 2,
              "filepaths": [{"filepath_id": aid * 10, "full_path": files[aid], "filename": f"art{aid}.biom"}]}
             for aid, vis in ((10, "public"), (11, "private"))]
    state = {"graph": graph, "cache": None, "fetches": 0, "detail_reads": 0}

    def _fetch_graph(study_id):
        state["fetches"] += 1
        return state["graph"]

    def _detail(study_id):
        state["detail_reads"] += 1
        return [], [{"artifact_id": a, "full_path": files[a]} for a in files]
    monkeypatch.setattr(mh, "public_artifact_ids", lambda sid: {10} if sid == 1 else set())
    monkeypatch.setattr(mh, "fetch_artifact_graph", _fetch_graph)
    monkeypatch.setattr(mh, "get_study_detail_cache", lambda sid: state["cache"])
    monkeypatch.setattr(mh, "_fetch_study_detail_from_qiita", _detail)
    monkeypatch.setattr(mh, "upsert_study_detail_cache", lambda *a, **k: None)
    monkeypatch.setattr(ar, "get_biom_sample_ids", lambda aid, path: [f"s{aid}a", f"s{aid}b"])
    monkeypatch.setattr(ar, "_qiita_fetch", lambda sql, args: [])
    state["mh"] = mh
    return state


def _download(client, aid, study_id=1):
    return client.get(f"/api/artifacts/{aid}/files/{aid * 10}/download?study_id={study_id}")


def test_public_artifact_downloads(client, qiita):
    resp = _download(client, 10)
    assert resp.status_code == 200 and resp.data == b"artifact 10"


def test_private_artifact_in_public_study_refused(client, qiita):
    assert _download(client, 11).status_code == 403


def test_private_study_refused(client, qiita):
    assert _download(client, 10, study_id=2).status_code == 403
    assert qiita["fetches"] == 0                 # refused before any graph lookup


def test_stale_cached_visibility_is_not_trusted(client, qiita):
    """The cache says artifact 11 is public (it was, hours ago); Qiita says it isn't."""
    stale = [{**n, "visibility": "public"} for n in qiita["graph"]]
    qiita["cache"] = {"artifact_graph_json": json.dumps(stale)}
    assert _download(client, 11).status_code == 403
    assert _download(client, 10).status_code == 200
    assert qiita["fetches"] == 0                 # the cached file lists are still used


def test_samples_only_for_public_artifacts(client, qiita):
    ok = client.get("/api/artifacts/10/samples?study_id=1")
    assert ok.status_code == 200 and [r["sample_id"] for r in ok.get_json()] == ["s10a", "s10b"]
    assert client.get("/api/artifacts/11/samples?study_id=1").status_code == 404
    assert client.get("/api/artifacts/10/samples?study_id=2").status_code == 404


def test_sample_counts_skip_non_public(client, qiita):
    resp = client.post("/api/artifacts/sample-counts", json={"study_id": 1, "artifact_ids": [10, 11]},
                       headers=client.csrf)
    assert resp.get_json() == {"10": 2}
    resp = client.post("/api/artifacts/sample-counts", json={"study_id": 2, "artifact_ids": [10]},
                       headers=client.csrf)
    assert resp.get_json() == {}


def test_get_artifacts_is_the_choke_point(client, qiita):
    """Every merge-workspace path (validate, samples, jobs) reads artifacts here."""
    mh = qiita["mh"]
    assert [a["artifact_id"] for a in mh._get_artifacts(1)] == [10]
    assert mh._get_artifacts(2) == []
    assert qiita["detail_reads"] == 1            # the private study never reached Qiita's tables


def test_merge_workspace_refuses_private_study(client, qiita, monkeypatch):
    import routes.merge_routes as mr
    monkeypatch.setattr(mr, "is_study_public", lambda sid: sid == 1)
    ws = client.post("/api/merge-workspaces", json={"name": "WS"}, headers=client.csrf).get_json()
    base = f"/api/merge-workspaces/{ws['workspace_id']}/studies"
    assert client.post(base, json={"study_id": 2}, headers=client.csrf).status_code == 404
    assert client.post(base, json={"study_id": "x"}, headers=client.csrf).status_code == 400
    assert client.post(base, json={"study_id": 1, "data_types": "16S"}, headers=client.csrf).status_code == 201


def test_study_detail_shows_only_what_qiita_shows_non_owners(client, monkeypatch):
    """TKT-105: prep 2 is sandboxed. Its prep, its artifacts and the processing
    step that led only to them are left out — not just their paths."""
    import routes.study_routes as sr
    sd = sr.study_detail
    graph = [
        {"kind": "artifact", "node_id": "a10", "parent_node_id": None, "artifact_id": 10, "prep_template_id": 1,
         "filepaths": [{"filepath_id": 100, "full_path": "/q/10"}]},
        {"kind": "job", "node_id": "j1", "parent_node_id": "a10"},
        {"kind": "artifact", "node_id": "a12", "parent_node_id": "j1", "artifact_id": 12, "filepaths": []},
        {"kind": "artifact", "node_id": "a11", "parent_node_id": None, "artifact_id": 11, "prep_template_id": 2,
         "filepaths": [{"filepath_id": 110, "full_path": "/q/11"}]},
        {"kind": "job", "node_id": "j2", "parent_node_id": "a11", "command_params": {"secret": 1}},
        {"kind": "artifact", "node_id": "a13", "parent_node_id": "j2", "artifact_id": 13, "filepaths": []},
        # public, under a private parent (not seen in Qiita, which sets a whole network at once): becomes a root
        {"kind": "artifact", "node_id": "a14", "parent_node_id": "a11", "artifact_id": 14, "filepaths": []},
    ]
    monkeypatch.setattr(sr, "is_study_public", lambda sid: True)
    monkeypatch.setattr(sd, "get_study_detail_cache", lambda sid: None)
    monkeypatch.setattr(sd, "upsert_study_detail_cache", lambda *a, **k: None)
    monkeypatch.setattr(sd, "_fetch_study_detail_from_qiita", lambda sid: (
        [{"prep_template_id": 1, "data_type": "16S"}, {"prep_template_id": 2, "data_type": "Metagenomic"}],
        [{"artifact_id": 10, "full_path": "/q/10.biom"}, {"artifact_id": 11, "full_path": "/q/11.biom"}]))
    monkeypatch.setattr(sd, "fetch_artifact_graph", lambda sid: graph)
    monkeypatch.setattr(sd, "_fetch_prep_metadata_summary", lambda pid: {})
    monkeypatch.setattr(sd, "_fetch_study_samples", lambda sid, limit: ([], 0))
    monkeypatch.setattr(sd, "_fetch_sample_context_text", lambda sid: "")
    monkeypatch.setattr(sd, "prep_membership", lambda sid: {})
    monkeypatch.setattr(sd, "public_artifact_ids", lambda sid: {10, 12, 14})
    monkeypatch.setattr(sd, "public_prep_ids", lambda sid: {1})
    body = client.get("/api/studies/5/detail").get_json()
    assert [p["prep_template_id"] for p in body["preps"]] == [1]
    assert [a["artifact_id"] for a in body["artifacts"]] == [10]
    nodes = {n["node_id"]: n for n in body["artifact_graph"]}
    assert set(nodes) == {"a10", "j1", "a12", "a14"}
    assert nodes["a10"]["filepaths"] == [{"filepath_id": 100, "full_path": "/q/10"}]
    assert nodes["a12"]["parent_node_id"] == "j1" and nodes["a14"]["parent_node_id"] is None
    assert "/q/11" not in json.dumps(body) and "110" not in json.dumps(body["artifact_graph"])
    # the chat's study tools read the same filtered copy
    preps, graph_out = sd.load_preps_and_graph(5)
    assert [p["prep_template_id"] for p in preps] == [1] and {n["node_id"] for n in graph_out} == set(nodes)


def test_raw_download_follows_qiitas_public_raw_download(client, qiita, monkeypatch):
    """Qiita serves a public study's raw sequences only when public_raw_download is on
    (otherwise only to its owners, whom QiitaExplore doesn't resolve)."""
    import routes.artifact_routes as ar
    qiita["graph"][0]["artifact_type"] = "per_sample_FASTQ"
    monkeypatch.setattr(ar, "raw_download_allowed", lambda sid: False)
    assert _download(client, 10).status_code == 403
    monkeypatch.setattr(ar, "raw_download_allowed", lambda sid: True)
    assert _download(client, 10).status_code == 200


def test_processed_download_ignores_public_raw_download(client, qiita, monkeypatch):
    import routes.artifact_routes as ar
    qiita["graph"][0]["artifact_type"] = "BIOM"
    monkeypatch.setattr(ar, "raw_download_allowed", lambda sid: False)
    assert _download(client, 10).status_code == 200


def test_study_detail_cache_keeps_everything_and_hits_are_filtered(client, monkeypatch):
    """The cache holds the whole study; a miss and then a hit both answer public
    only, and nothing filtered is ever written back (a later visibility change
    must show without waiting out the cache)."""
    import routes.study_routes as sr
    sd = sr.study_detail
    cache = {}

    def upsert(sid, preps_json, artifacts_json, **kw):
        row = cache.setdefault(sid, {})
        row.update({k: v for k, v in dict(preps_json=preps_json, artifacts_json=artifacts_json, **kw).items()
                    if v is not None})
    graph = [{"kind": "artifact", "node_id": f"a{aid}", "parent_node_id": None, "artifact_id": aid,
              "prep_template_id": pid, "visibility": vis, "paths_v": 2, "filepaths": []}
             for aid, pid, vis in ((10, 1, "public"), (11, 2, "sandbox"))]
    monkeypatch.setattr(sr, "is_study_public", lambda sid: True)
    monkeypatch.setattr(sd, "get_study_detail_cache", lambda sid: dict(cache[sid]) if sid in cache else None)
    monkeypatch.setattr(sd, "upsert_study_detail_cache", upsert)
    monkeypatch.setattr(sd, "_fetch_study_detail_from_qiita", lambda sid: (
        [{"prep_template_id": 1}, {"prep_template_id": 2}], [{"artifact_id": 10}, {"artifact_id": 11}]))
    monkeypatch.setattr(sd, "fetch_artifact_graph", lambda sid: graph)
    monkeypatch.setattr(sd, "_fetch_prep_metadata_summary", lambda pid: {"platform": "Illumina"})
    monkeypatch.setattr(sd, "_fetch_study_samples", lambda sid, limit: ([], 0))
    monkeypatch.setattr(sd, "_fetch_sample_context_text", lambda sid: "ctx")
    monkeypatch.setattr(sd, "prep_membership", lambda sid: {})
    monkeypatch.setattr(sd, "public_artifact_ids", lambda sid: {10})
    monkeypatch.setattr(sd, "public_prep_ids", lambda sid: {1})
    for hit in (False, True):
        body = client.get("/api/studies/6/detail").get_json()
        assert body["cached"] is hit
        assert [p["prep_template_id"] for p in body["preps"]] == [1]
        assert [a["artifact_id"] for a in body["artifacts"]] == [10]
        assert [n["artifact_id"] for n in body["artifact_graph"]] == [10]
    assert [p["prep_template_id"] for p in json.loads(cache[6]["preps_json"])] == [1, 2]
    assert len(json.loads(cache[6]["artifacts_json"])) == 2 and len(json.loads(cache[6]["artifact_graph_json"])) == 2


def test_visibility_reads_fail_closed(monkeypatch):
    stub_qiita_db_and_core()
    import helpers.qiita_fetch as qf

    def down(*a, **k):
        raise RuntimeError("Qiita is down")
    monkeypatch.setattr(qf, "pooled_fetchall", down)
    assert qf.public_prep_ids(1) == set() and qf.public_artifact_ids(1) == set()
    assert qf.raw_download_allowed(1) is False


def test_workspace_preps_stored_before_the_fix_are_cleared_once():
    """store/db.py :: _clear_workspace_preps_v1: rows enriched before TKT-105 lose
    their unfiltered preps_json; one written afterwards survives a restart."""
    import sqlite3
    from store import db
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE project_studies (study_id INTEGER, preps_json TEXT)")
    conn.execute("""INSERT INTO project_studies VALUES (10317, '[{"prep_template_id": 19306}]')""")
    db._clear_workspace_preps_v1(conn)
    assert conn.execute("SELECT preps_json FROM project_studies").fetchone()[0] is None
    conn.execute("UPDATE project_studies SET preps_json = '[]'")
    db._clear_workspace_preps_v1(conn)
    assert conn.execute("SELECT preps_json FROM project_studies").fetchone()[0] == "[]"


def test_file_listing_sql_is_public_only():
    """FASTQ manifest, aggregation export and file map all build on _FILES_FROM;
    the aggregation card's sequence-artifact count is public only too."""
    from helpers import fastq_manifest as fm
    for sql in (fm._ARTIFACT_FILES_SQL, fm._STUDIES_FILES_SQL, fm._COUNT_SQL):
        assert "JOIN qiita.visibility v" in sql and "v.visibility = 'public'" in sql


def test_prep_counts_and_data_type_search_count_only_public_preps():
    """TKT-105: card counts and data-type chips, the data-type facet, and the
    data-type search filter all skip preps without a public artifact."""
    stub_qiita_db_and_core()
    from helpers import qiita_fetch as qf
    from services import browse_filters as bf, study_service as ss
    snippet = qf._public_prep_exists("spt")
    assert "qiita.preparation_artifact" in snippet and "visibility = 'public'" in snippet
    dt_sql, params = ss.build_data_type_filter(["16S"], ["WGS"])
    assert params == ["16S", "WGS"] and snippet in dt_sql
    assert snippet in bf._DATA_TYPE_FACET_SQL
    for alias in ("spt2", "spt3"):
        assert qf._public_prep_exists(alias) in qf._STUDY_COUNT_COLUMNS


def test_workspace_keeps_only_public_preps(monkeypatch):
    stub_qiita_db_and_core()
    import helpers.workspace_studies as ws
    preps = [{"prep_template_id": 1, "data_type": "16S"}, {"prep_template_id": 2, "data_type": "Metagenomic"}]
    saved = {}
    monkeypatch.setattr(ws, "_qiita_fetch", lambda sql, params: [(40,)])
    monkeypatch.setattr(ws, "get_study_detail_cache",
                        lambda sid: {"preps_json": json.dumps(preps), "samples_context": "ctx"})
    monkeypatch.setattr(ws, "public_prep_ids", lambda sid: {1})
    monkeypatch.setattr(ws, "update_project_study_data", lambda pid, sid, **kw: saved.update(kw))
    ws.enrich_study_in_project("p1", 5)
    assert (saved["num_preps"], saved["data_types"]) == (1, "16S")
    assert [p["prep_template_id"] for p in json.loads(saved["preps_json"])] == [1]
