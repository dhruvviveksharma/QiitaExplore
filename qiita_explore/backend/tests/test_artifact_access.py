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


def test_study_detail_strips_private_artifact_paths(client, monkeypatch):
    import routes.study_routes as sr
    sd = sr.study_detail
    graph = [{"kind": "artifact", "artifact_id": 10, "full_path": "/q/10.biom", "filepaths": [{"full_path": "/q/10"}]},
             {"kind": "artifact", "artifact_id": 11, "full_path": "/q/11.biom", "filepaths": [{"full_path": "/q/11"}]},
             {"kind": "job", "node_id": "j1"}]
    monkeypatch.setattr(sr, "is_study_public", lambda sid: True)
    monkeypatch.setattr(sd, "get_study_detail_cache", lambda sid: None)
    monkeypatch.setattr(sd, "upsert_study_detail_cache", lambda *a, **k: None)
    monkeypatch.setattr(sd, "_fetch_study_detail_from_qiita", lambda sid: (
        [], [{"artifact_id": 10, "full_path": "/q/10.biom"}, {"artifact_id": 11, "full_path": "/q/11.biom"}]))
    monkeypatch.setattr(sd, "fetch_artifact_graph", lambda sid: graph)
    monkeypatch.setattr(sd, "_fetch_study_samples", lambda sid, limit: ([], 0))
    monkeypatch.setattr(sd, "_fetch_sample_context_text", lambda sid: "")
    monkeypatch.setattr(sd, "prep_membership", lambda sid: {})
    monkeypatch.setattr(sd, "public_artifact_ids", lambda sid: {10})
    body = client.get("/api/studies/5/detail").get_json()
    assert [a["full_path"] for a in body["artifacts"]] == ["/q/10.biom", None]
    nodes = {n.get("artifact_id"): n for n in body["artifact_graph"]}
    assert nodes[10]["filepaths"] == [{"full_path": "/q/10"}]
    assert nodes[11]["filepaths"] == [] and nodes[11]["full_path"] is None
    assert nodes[None] == {"kind": "job", "node_id": "j1"}


def test_file_listing_sql_is_public_only():
    """FASTQ manifest, aggregation export and file map all build on _FILES_FROM."""
    from helpers import fastq_manifest as fm
    for sql in (fm._ARTIFACT_FILES_SQL, fm._STUDIES_FILES_SQL):
        assert "JOIN qiita.visibility v" in sql and "v.visibility = 'public'" in sql
