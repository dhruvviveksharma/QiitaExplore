"""Study-card prep routes: /sample-preps, /preps/<pid>/samples, and the prep_ids
attached to /detail's samples. Postgres-touching names are patched on the route
module, or on helpers/study_detail.py through the route's binding to it (same app
fixture pattern as test_fastq_manifest)."""
import os
import sys

import pytest

from .conftest import stub_qiita_db_and_core


@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = str(tmp_path_factory.mktemp("study_prep") / "test.db")
    for name in list(sys.modules):
        if (name == "run" or name.startswith("routes.") or name == "store"
                or name.startswith("store.") or name.startswith("helpers.")
                or "sql_store" in name):
            del sys.modules[name]
    stub_qiita_db_and_core()
    import run
    import config
    config.ALLOWED_ORIGINS = []
    config.SESSION_COOKIE_SECURE = False
    return run.app


@pytest.fixture
def client(_app):
    return _app.test_client()


@pytest.fixture
def sr(client, monkeypatch):
    import routes.auth_routes as auth_routes
    import routes.study_routes as mod
    from helpers.qiita_client import WhoAmIResult

    monkeypatch.setattr(auth_routes, "whoami", lambda pat: WhoAmIResult(ok=True, identity={
        "principal_idx": 90004, "email": "prep@test.local",
        "system_role": "user", "scopes": [], "profile_complete": True,
    }))
    assert client.post("/api/auth/connect", json={"token": "qk_test"}).status_code == 200
    monkeypatch.setattr(mod, "is_study_public", lambda sid: sid != 99999)
    return mod


def test_sample_preps(client, sr, monkeypatch):
    groups = [{"prep_id": 5, "data_type": "16S", "num_samples": 2},
              {"prep_id": None, "data_type": None, "num_samples": 1}]
    monkeypatch.setattr(sr, "prep_groups", lambda sid: groups)
    assert client.get("/api/studies/14382/sample-preps").get_json() == {"groups": groups}
    assert client.get("/api/studies/99999/sample-preps").status_code == 404


def test_prep_samples_parses_prep_id_and_clamps_limit(client, sr, monkeypatch):
    seen = []

    def fake(sid, pid, limit):
        seen.append((sid, pid, limit))
        return [{"sample_id": "s1", "prep_ids": [5]}], 1
    monkeypatch.setattr(sr, "fetch_prep_samples", fake)
    d = client.get("/api/studies/14382/preps/5/samples").get_json()
    assert (d["prep_id"], d["total"], d["limit"], d["samples"][0]["sample_id"]) == (5, 1, 500, "s1")
    assert client.get("/api/studies/14382/preps/none/samples?limit=9999").get_json()["prep_id"] is None
    assert seen == [(14382, 5, 500), (14382, None, 500)]
    assert client.get("/api/studies/14382/preps/abc/samples").status_code == 400
    assert client.get("/api/studies/14382/preps/5/samples?limit=x").status_code == 400
    assert client.get("/api/studies/99999/preps/5/samples").status_code == 404


def test_detail_samples_carry_prep_ids(client, sr, monkeypatch):
    sid = 71001
    sr.study_detail.upsert_study_detail_cache(sid, '[{"prep_template_id": 7, "data_type": "16S"}]', "[]",
                                 samples_context="ctx", artifact_graph_json="[]", prep_metadata_json="{}",
                                 samples_json='[{"sample_id": "s1"}, {"sample_id": "s2"}]', total_samples=2)
    monkeypatch.setattr(sr.study_detail, "prep_membership", lambda s: {"s1": [(7, "16S"), (8, "WGS")]})
    d = client.get(f"/api/studies/{sid}/detail").get_json()
    assert [(x["sample_id"], x["prep_ids"]) for x in d["samples"]] == [("s1", [7, 8]), ("s2", [])]


@pytest.mark.parametrize("node, refetched", [
    ({"kind": "artifact", "node_id": "a1", "filepaths": []}, True),                              # pre-visibility cache
    ({"kind": "artifact", "node_id": "a1", "filepaths": [], "visibility": "public"}, True),     # pre-paths_v cache
    ({"kind": "artifact", "node_id": "a1", "filepaths": [], "visibility": "public", "paths_v": 2}, False),
])
def test_detail_refetches_graph_cached_before_visibility(client, sr, monkeypatch, node, refetched):
    """The study modal's chart hides archived artifacts by `visibility`, and file
    paths honour data_directory.subdirectory since `paths_v` (TKT-082), so a graph
    cached before either field existed is treated as stale and rebuilt."""
    import json
    sid = 71002 + len(node) if refetched else 71000
    sr.study_detail.upsert_study_detail_cache(sid, '[{"prep_template_id": 7, "data_type": "16S"}]', "[]",
                                 samples_context="ctx", artifact_graph_json=json.dumps([node]),
                                 prep_metadata_json="{}", samples_json="[]", total_samples=0)
    fresh = [{**node, "visibility": "public", "name": "rebuilt"}]
    calls = []
    monkeypatch.setattr(sr.study_detail, "fetch_artifact_graph", lambda s: calls.append(s) or fresh)
    monkeypatch.setattr(sr.study_detail, "prep_membership", lambda s: {})
    graph = client.get(f"/api/studies/{sid}/detail").get_json()["artifact_graph"]
    assert calls == ([sid] if refetched else [])
    assert graph == (fresh if refetched else [node])


def test_sample_detail_uses_pooled_lookup(client, sr, monkeypatch):
    monkeypatch.setattr(sr, "fetch_sample_fields", lambda sid, sample: {"a": 1} if sample == "s/1" else None)
    assert client.get("/api/studies/71004/samples/s/1").get_json() == {"sample_id": "s/1", "fields": {"a": 1}}
    assert client.get("/api/studies/71004/samples/nope").status_code == 404
    assert client.get("/api/studies/99999/samples/s/1").status_code == 404     # not public
