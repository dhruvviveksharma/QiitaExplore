"""Sample Aggregation: pure aggregate-row merging (helpers/fastq_manifest),
SQLite CRUD (store/aggregation_crud), and the /api/aggregations routes with
Qiita Postgres stubbed."""

import os
import sys

import pytest

from .conftest import stub_qiita_db_and_core

BASE = "/qmounts/qiita_data"


# ── pure: build_aggregate_rows ───────────────────────────────────────────────

@pytest.fixture
def fm():
    stub_qiita_db_and_core()
    import helpers.fastq_manifest as fm
    return fm


def _paired_group(sample_id, prefix, artifact_id):
    files = [
        ("raw_forward_seqs", "per_sample_FASTQ", True, artifact_id, f"{prefix}_R1_001.fastq.gz"),
        ("raw_reverse_seqs", "per_sample_FASTQ", True, artifact_id, f"{prefix}_R2_001.fastq.gz"),
    ]
    return [(sample_id, prefix)], files


def _single_group(sample_id, prefix, artifact_id):
    files = [("raw_forward_seqs", "raw_data", False, artifact_id, f"{artifact_id}_{prefix}.fastq.gz")]
    return [(sample_id, prefix)], files


def test_aggregate_concat_and_sort(fm):
    rows, paired = fm.build_aggregate_rows(
        [_single_group("s2", "SRR2", 20), _single_group("s1", "SRR1", 10)], BASE)
    assert paired is False
    assert rows == [
        ("s1", f"{BASE}/raw_data/10_SRR1.fastq.gz", None),
        ("s2", f"{BASE}/raw_data/20_SRR2.fastq.gz", None),
    ]


def test_aggregate_paired_if_any_group_paired(fm):
    rows, paired = fm.build_aggregate_rows(
        [_paired_group("s1", "P1", 10), _single_group("s2", "SRR2", 20)], BASE)
    assert paired is True
    assert rows[0] == ("s1", f"{BASE}/per_sample_FASTQ/10/P1_R1_001.fastq.gz",
                       f"{BASE}/per_sample_FASTQ/10/P1_R2_001.fastq.gz")
    assert rows[1] == ("s2", f"{BASE}/raw_data/20_SRR2.fastq.gz", None)
    lines = fm.to_tsv(rows, paired).split("\n")
    assert lines[0] == "sample-id\tforward-absolute-filepath\treverse-absolute-filepath"
    assert lines[2] == f"s2\t{BASE}/raw_data/20_SRR2.fastq.gz\t"


def test_aggregate_duplicate_sample_id_first_group_wins(fm):
    rows, _ = fm.build_aggregate_rows(
        [_single_group("s1", "SRR1", 10), _single_group("s1", "SRR1", 99)], BASE)
    assert rows == [("s1", f"{BASE}/raw_data/10_SRR1.fastq.gz", None)]


def test_aggregate_empty_groups(fm):
    assert fm.build_aggregate_rows([], BASE) == ([], False)


# ── store ────────────────────────────────────────────────────────────────────

@pytest.fixture
def agg_crud():
    import store.aggregation_crud as m
    return m


STUDY = {"study_id": 16326, "study_title": "Test", "data_types": "16S", "num_samples": 2, "num_preps": 1,
         "study_abstract": "About soil", "pi_name": "Rob Knight", "pi_affiliation": "UCSD",
         "year": 2015, "is_gold": True}
SAMPLES = ["s1", "s2"]


def test_store_create_list_get(agg_crud):
    agg = agg_crud.create_aggregation("u1", "A")
    listed = agg_crud.list_aggregations("u1")
    assert [a["aggregation_id"] for a in listed] == [agg["aggregation_id"]]
    assert listed[0]["studies"] == []
    assert agg_crud.get_aggregation(agg["aggregation_id"], "u2") is None
    assert agg_crud.list_aggregations("u2") == []


def test_store_add_readd_remove_study(agg_crud):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg = agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, SAMPLES)
    assert len(agg["studies"]) == 1
    s = agg["studies"][0]
    assert (s["study_id"], s["study_title"], s["data_types"], s["num_samples"], s["num_preps"],
            s["fastq_artifact_count"]) == (16326, "Test", "16S", 2, 1, 2)
    # header snapshot for the tab's cards
    assert (s["study_abstract"], s["pi_name"], s["pi_affiliation"], s["year"], s["is_gold"]) == \
        ("About soil", "Rob Knight", "UCSD", 2015, 1)
    assert s["selected_samples"] == 2
    assert len(agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, SAMPLES)["studies"]) == 1
    assert agg_crud.add_study_to_aggregation(aid, "u2", STUDY, 2, SAMPLES) is None
    assert agg_crud.remove_study_from_aggregation(aid, "u2", 16326) is None
    assert agg_crud.remove_study_from_aggregation(aid, "u1", 16326)["studies"] == []


def test_store_set_samples_add_remove_clear(agg_crud):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, SAMPLES)

    agg = agg_crud.set_aggregation_samples(aid, "u1", 16326, remove=["s2"])
    assert agg["studies"][0]["selected_samples"] == 1
    assert agg_crud.selected_in(aid, 16326, ["s1", "s2", "zz"]) == {"s1"}

    agg = agg_crud.set_aggregation_samples(aid, "u1", 16326, add=["s2", "s3"])
    assert agg["studies"][0]["selected_samples"] == 3
    assert agg_crud.selected_by_study(aid) == {16326: {"s1", "s2", "s3"}}

    agg = agg_crud.set_aggregation_samples(aid, "u1", 16326, clear=True, add=["s9"])
    assert agg["studies"][0]["selected_samples"] == 1
    assert agg_crud.selected_in(aid, 16326, ["s9"]) == {"s9"}
    assert agg_crud.selected_in(aid, 16326, []) == set()

    assert agg_crud.set_aggregation_samples(aid, "u1", 16326, clear=True)["studies"][0]["selected_samples"] == 0
    assert agg_crud.set_aggregation_samples(aid, "u2", 16326, add=["s1"]) is None


def test_store_readd_keeps_deselection(agg_crud):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, SAMPLES)
    agg_crud.set_aggregation_samples(aid, "u1", 16326, remove=["s2"])
    # Re-adding a study already present must not re-check what the user unchecked.
    agg = agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, SAMPLES)
    assert agg["studies"][0]["selected_samples"] == 1


def test_store_rename(agg_crud):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    assert agg_crud.rename_aggregation(aid, "u1", "B")["name"] == "B"
    assert agg_crud.rename_aggregation(aid, "u2", "C") is None
    assert agg_crud.get_aggregation(aid, "u1")["name"] == "B"


def test_store_delete_cascades(agg_crud, db_conn):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 1, SAMPLES)
    assert db_conn.execute("SELECT COUNT(*) FROM aggregation_samples").fetchone()[0] == 2
    assert agg_crud.delete_aggregation(aid, "u2") is False
    assert agg_crud.delete_aggregation(aid, "u1") is True
    assert agg_crud.list_aggregations("u1") == []
    assert db_conn.execute("SELECT COUNT(*) FROM aggregation_studies").fetchone()[0] == 0
    # chain cascade: aggregation -> studies -> samples
    assert db_conn.execute("SELECT COUNT(*) FROM aggregation_samples").fetchone()[0] == 0


def test_store_remove_study_cascades_samples(agg_crud, db_conn):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 1, SAMPLES)
    agg_crud.remove_study_from_aggregation(aid, "u1", 16326)
    assert db_conn.execute("SELECT COUNT(*) FROM aggregation_samples").fetchone()[0] == 0


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("aggregations") / "test.db")
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = db_path
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
def logged_in(client, monkeypatch):
    """Session cookie on `client`; returns the CSRF header dict for state-changing calls."""
    import routes.auth_routes as auth_routes
    from helpers.qiita_client import WhoAmIResult

    monkeypatch.setattr(auth_routes, "whoami", lambda pat: WhoAmIResult(ok=True, identity={
        "principal_idx": 90003, "email": "agg@test.local",
        "system_role": "user", "scopes": [], "profile_complete": True,
    }))
    resp = client.post("/api/auth/connect", json={"token": "qk_test"})
    assert resp.status_code == 200, resp.get_json()
    return {"X-CSRF-Token": resp.get_json()["csrf_token"]}


@pytest.fixture
def stub_qiita(monkeypatch):
    """Every Postgres-touching name the routes import, patched on the route module."""
    import routes.aggregation_routes as ar
    monkeypatch.setattr(ar, "is_study_public", lambda sid: sid != 99999)
    monkeypatch.setattr(ar, "count_fastq_artifacts", lambda sid: 2)
    monkeypatch.setattr(ar, "list_study_sample_ids", lambda sid: ["s1", "s2"])
    monkeypatch.setattr(ar, "matching_sample_ids", lambda sid, q: ["s2"])
    monkeypatch.setattr(ar, "fetch_sample_page",
                        lambda sid, offset, limit, q=None: ([("s1", "stool"), ("s2", "skin")], 2, ["sample_type"]))
    monkeypatch.setattr(ar, "fetch_aggregate_manifest",
                        lambda ids: ([("s1", "/a/f_R1.fq.gz", "/a/f_R2.fq.gz")], True))
    return ar


def _create(client, hdr, name="Agg"):
    r = client.post("/api/aggregations", json={"name": name}, headers=hdr)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _add(client, hdr, aid, study_id=16326):
    return client.post(f"/api/aggregations/{aid}/studies", headers=hdr,
                       json={"study": {**STUDY, "study_id": study_id}})


def _listed_ids(client):
    return [a["aggregation_id"] for a in client.get("/api/aggregations").get_json()["aggregations"]]


def test_route_crud_roundtrip(client, logged_in, stub_qiita):
    agg = _create(client, logged_in, "Round trip")
    aid = agg["aggregation_id"]
    assert agg["studies"] == []
    assert aid in _listed_ids(client)

    r = client.patch(f"/api/aggregations/{aid}", json={"name": "Renamed"}, headers=logged_in)
    assert r.status_code == 200 and r.get_json()["name"] == "Renamed"

    r = _add(client, logged_in, aid)
    assert r.status_code == 200, r.get_json()
    studies = r.get_json()["studies"]
    assert [s["study_id"] for s in studies] == [16326]
    assert studies[0]["fastq_artifact_count"] == 2
    # whole study added = every sample checked; badge denominator = rows stored
    assert (studies[0]["selected_samples"], studies[0]["num_samples"]) == (2, 2)
    assert studies[0]["pi_name"] == "Rob Knight"

    r = client.delete(f"/api/aggregations/{aid}/studies/16326", headers=logged_in)
    assert r.status_code == 200 and r.get_json()["studies"] == []

    r = client.delete(f"/api/aggregations/{aid}", headers=logged_in)
    assert r.status_code == 200 and r.get_json() == {"deleted": aid}
    assert aid not in _listed_ids(client)


def test_route_rename_blank_400_and_unknown_404(client, logged_in, stub_qiita):
    aid = _create(client, logged_in)["aggregation_id"]
    assert client.patch(f"/api/aggregations/{aid}", json={"name": "  "}, headers=logged_in).status_code == 400
    assert client.patch("/api/aggregations/nope", json={"name": "x"}, headers=logged_in).status_code == 404
    assert client.delete("/api/aggregations/nope", headers=logged_in).status_code == 404


def test_route_add_study_errors(client, logged_in, stub_qiita, monkeypatch):
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid, study_id=99999).status_code == 403
    assert _add(client, logged_in, "nope").status_code == 404
    r = client.post(f"/api/aggregations/{aid}/studies", json={"study": {}}, headers=logged_in)
    assert r.status_code == 400
    monkeypatch.setattr(stub_qiita, "AGGREGATION_STUDIES_CAP", 1)
    assert _add(client, logged_in, aid, study_id=1).status_code == 200
    r = _add(client, logged_in, aid, study_id=2)
    assert r.status_code == 400 and "maximum" in r.get_json()["error"]


def test_route_samples_page(client, logged_in, stub_qiita):
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    client.patch(f"/api/aggregations/{aid}/studies/16326/samples", json={"remove": ["s2"]}, headers=logged_in)

    r = client.get(f"/api/aggregations/{aid}/studies/16326/samples?offset=0&limit=50&q=s")
    assert r.status_code == 200, r.get_json()
    page = r.get_json()
    assert (page["study_id"], page["total"], page["offset"], page["limit"]) == (16326, 2, 0, 50)
    assert page["columns"] == ["sample_type"]
    assert page["selected_count"] == 1
    assert page["rows"] == [
        {"sample_id": "s1", "selected": True, "fields": {"sample_type": "stool"}},
        {"sample_id": "s2", "selected": False, "fields": {"sample_type": "skin"}},
    ]
    # limit is clamped, offset floors at 0
    page = client.get(f"/api/aggregations/{aid}/studies/16326/samples?limit=9999&offset=-5").get_json()
    assert (page["limit"], page["offset"]) == (500, 0)

    assert client.get(f"/api/aggregations/{aid}/studies/16326/samples?offset=x").status_code == 400
    assert client.get(f"/api/aggregations/{aid}/studies/777/samples").status_code == 404
    assert client.get("/api/aggregations/nope/studies/16326/samples").status_code == 404


def test_route_set_samples_variants(client, logged_in, stub_qiita):
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    url = f"/api/aggregations/{aid}/studies/16326/samples"

    def count(resp):
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()["studies"][0]["selected_samples"]

    assert count(client.patch(url, json={"select": "none"}, headers=logged_in)) == 0
    assert count(client.patch(url, json={"add": ["s1"]}, headers=logged_in)) == 1
    assert count(client.patch(url, json={"select": "matching", "q": "skin"}, headers=logged_in)) == 2
    assert count(client.patch(url, json={"remove": ["s1", "s2"]}, headers=logged_in)) == 0
    assert count(client.patch(url, json={"select": "all"}, headers=logged_in)) == 2

    for bad in [{}, {"add": "s1"}, {"add": [1]}, {"select": "matching"}, {"select": "some"}]:
        r = client.patch(url, json=bad, headers=logged_in)
        assert r.status_code == 400, bad
    assert client.patch(f"/api/aggregations/{aid}/studies/777/samples",
                        json={"select": "all"}, headers=logged_in).status_code == 404
    assert client.patch(url, json={"select": "all"}).status_code == 403   # no CSRF header


def test_route_manifest_tsv(client, logged_in, stub_qiita):
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    resp = client.get(f"/api/aggregations/{aid}/manifest")
    assert resp.status_code == 200
    assert resp.mimetype == "text/tab-separated-values"
    assert resp.headers["Content-Disposition"] == f"attachment; filename=manifest_aggregation_{aid}.tsv"
    body = resp.get_data(as_text=True).split("\n")
    assert body[0] == "sample-id\tforward-absolute-filepath\treverse-absolute-filepath"
    assert body[1] == "s1\t/a/f_R1.fq.gz\t/a/f_R2.fq.gz"


def test_route_manifest_errors(client, logged_in, stub_qiita, monkeypatch):
    aid = _create(client, logged_in)["aggregation_id"]
    assert client.get(f"/api/aggregations/{aid}/manifest").status_code == 400  # no studies
    assert client.get("/api/aggregations/nope/manifest").status_code == 404

    def _raise(ids):
        raise ValueError("No per_sample_FASTQ artifacts in these studies")
    monkeypatch.setattr(stub_qiita, "fetch_aggregate_manifest", _raise)
    assert _add(client, logged_in, aid).status_code == 200
    resp = client.get(f"/api/aggregations/{aid}/manifest")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "No per_sample_FASTQ artifacts in these studies"


def test_route_401_without_session(_app):
    assert _app.test_client().get("/api/aggregations").status_code == 401


def test_route_post_requires_csrf(client, logged_in):
    assert client.post("/api/aggregations", json={"name": "x"}).status_code == 403
