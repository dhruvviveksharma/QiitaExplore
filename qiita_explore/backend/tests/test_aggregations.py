"""Sample Aggregation: the /api/aggregations routes (CRUD, per-row samples page and
selection, export) with Qiita Postgres stubbed; the SQLite CRUD is in
test_aggregation_store.py and the filter / group / sort routes in
test_aggregation_filters.py. The pure export row builder is in test_fastq_manifest.py."""

import csv
import os
import sys

import pytest

from .conftest import stub_qiita_db_and_core


STUDY = {"study_id": 16326, "study_title": "Test", "data_types": "16S", "num_samples": 2, "num_preps": 1,
         "study_abstract": "About soil", "pi_name": "Rob Knight", "pi_affiliation": "UCSD",
         "year": 2015, "is_gold": True}


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


# s2 is in two artifacts (12 -> prep 5, 13 -> prep 6): two checkable rows, one sample.
_S2_FILES = [{"artifact_id": 12, "data_type": "16S", "processing": "Raw upload",
              "r1": "/a/f12_R1.fq.gz", "r2": "/a/f12_R2.fq.gz", "barcodes": ""},
             {"artifact_id": 13, "data_type": "16S", "processing": "Raw upload",
              "r1": "/a/f13_R1.fq.gz", "r2": "", "barcodes": ""}]
_S2_ENTRIES = [("16S", "Raw upload", 12, 2, 0), ("16S", "Raw upload", 13, 1, 0)]


@pytest.fixture
def stub_qiita(monkeypatch):
    """Every Postgres-touching name the routes import, patched on the route
    module. s2 is the one sample with files (artifacts 12 paired, 13 single), s1
    has none (a placeholder row), so tests can exercise files-first ordering /
    show filtering / "select: with_files" / per-row selection without a real
    availability computation."""
    import routes.aggregation_routes as ar
    _FIELDS = {"s1": ("s1", "stool"), "s2": ("s2", "skin")}
    monkeypatch.setattr(ar, "is_study_public", lambda sid: sid != 99999)
    monkeypatch.setattr(ar, "count_fastq_artifacts", lambda sid: 2)
    monkeypatch.setattr(ar, "list_study_sample_ids", lambda sid: ["s1", "s2"])
    monkeypatch.setattr(ar, "matching_sample_ids", lambda sid, q: ["s2"])
    monkeypatch.setattr(ar, "display_columns", lambda sid: ["sample_type"])
    monkeypatch.setattr(ar, "fetch_samples_by_ids", lambda sid, ids: [_FIELDS[i] for i in ids if i in _FIELDS])
    monkeypatch.setattr(ar, "get_sample_files", lambda sid: {"s2": list(_S2_ENTRIES)})
    monkeypatch.setattr(ar, "artifact_preps", lambda sid: {12: 5, 13: 6})
    monkeypatch.setattr(ar, "prep_data_types", lambda sid: {})
    monkeypatch.setattr(ar, "prep_membership", lambda sid: {})
    monkeypatch.setattr(ar, "page_files", lambda sid, ids, all_files, file_filter:
                        {i: _S2_FILES if i == "s2" else [] for i in ids})
    monkeypatch.setattr(ar, "fetch_export_rows", lambda selected, file_filters=None: [
        (16326, "s1", 12, "16S", "Raw upload", "/a/f12_R1.fq.gz", "/a/f12_R2.fq.gz", ""),
    ])
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
    # whole study added = every (sample, artifact) row checked; badge denominator = file rows
    assert (studies[0]["selected_rows"], studies[0]["file_rows"], studies[0]["num_samples"]) == (2, 2, 2)
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


def _row(sample_id, artifact_id):
    return {"sample_id": sample_id, "artifact_id": artifact_id}


def test_route_samples_page(client, logged_in, stub_qiita):
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    base = f"/api/aggregations/{aid}/studies/16326/samples"
    # Row 13 unchecked on its own: the other row of the same sample stays checked.
    client.patch(base, json={"remove": [_row("s2", 13)]}, headers=logged_in)

    r = client.get(base + "?offset=0&limit=50")
    assert r.status_code == 200, r.get_json()
    page = r.get_json()
    assert (page["study_id"], page["total"], page["offset"], page["limit"]) == (16326, 3, 0, 50)
    assert page["columns"] == ["sample_type"]
    assert page["selected_count"] == 1
    assert page["with_files"] == 2                                   # file rows, not samples
    # files-first: s2's two rows (by artifact) before s1's placeholder
    assert [(row["sample_id"], row["artifact_id"]) for row in page["rows"]] == [("s2", 12), ("s2", 13), ("s1", None)]
    assert page["rows"][0] == {"sample_id": "s2", "artifact_id": 12, "prep_id": 5, "selected": True,
                                "fastq": "paired", "fasta": False, "data_types": ["16S"],
                                "file_data_types": ["16S"], "processing": ["Raw upload"], "prep_ids": [5],
                                "file": {"r1": "/a/f12_R1.fq.gz", "r2": "/a/f12_R2.fq.gz", "barcodes": ""},
                                "fields": {"sample_type": "skin"}}
    second = page["rows"][1]
    assert (second["selected"], second["prep_id"], second["fastq"]) == (False, 6, "single")
    assert page["rows"][2] == {"sample_id": "s1", "artifact_id": None, "prep_id": None, "selected": False,
                                "fastq": None, "fasta": False, "data_types": [], "file_data_types": [],
                                "processing": [], "prep_ids": [], "file": None,
                                "fields": {"sample_type": "stool"}}

    only_files = client.get(base + "?show=with_files").get_json()
    assert [(row["sample_id"], row["artifact_id"]) for row in only_files["rows"]] == [("s2", 12), ("s2", 13)]
    assert only_files["total"] == 2

    without_files = client.get(base + "?show=without_files").get_json()
    assert [row["sample_id"] for row in without_files["rows"]] == ["s1"]
    assert without_files["total"] == 1

    assert client.get(base + "?show=bogus").status_code == 400

    # q routes to matching_sample_ids (stub returns only s2, regardless of q)
    q_page = client.get(base + "?q=x").get_json()
    assert [row["sample_id"] for row in q_page["rows"]] == ["s2", "s2"]
    assert q_page["total"] == 2

    # paging is over rows: the second page of one row is s2's second artifact
    paged = client.get(base + "?offset=1&limit=1").get_json()
    assert [(row["sample_id"], row["artifact_id"]) for row in paged["rows"]] == [("s2", 13)]

    # limit is clamped, offset floors at 0
    page = client.get(base + "?limit=9999&offset=-5").get_json()
    assert (page["limit"], page["offset"]) == (500, 0)

    assert client.get(base + "?offset=x").status_code == 400
    assert client.get(f"/api/aggregations/{aid}/studies/777/samples").status_code == 404
    assert client.get("/api/aggregations/nope/studies/16326/samples").status_code == 404


def test_route_set_rows_variants(client, logged_in, stub_qiita):
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    url = f"/api/aggregations/{aid}/studies/16326/samples"

    def count(resp):
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()["studies"][0]["selected_rows"]

    assert count(client.patch(url, json={"select": "none"}, headers=logged_in)) == 0
    assert count(client.patch(url, json={"add": [_row("s2", 12)]}, headers=logged_in)) == 1
    # one row of s2 on its own — never the whole sample
    assert count(client.patch(url, json={"select": "matching", "q": "skin"}, headers=logged_in)) == 2
    assert count(client.patch(url, json={"remove": [_row("s2", 12), _row("s2", 13)]}, headers=logged_in)) == 0
    assert count(client.patch(url, json={"select": "all"}, headers=logged_in)) == 2   # s1 has no row to check
    # "with_files" replaces the whole selection with the rows under the study's filter —
    # exactly what the export can contain.
    client.patch(f"/api/aggregations/{aid}/studies/16326", json={"file_filter": {"artifacts": ["13"]}},
                 headers=logged_in)
    assert count(client.patch(url, json={"select": "with_files"}, headers=logged_in)) == 1
    assert count(client.patch(url, json={"select": "with_files", "q": "skin"}, headers=logged_in)) == 1

    for bad in [{}, {"add": "s1"}, {"add": ["s1"]}, {"add": [{"sample_id": "s1"}]},
                {"add": [{"sample_id": "s1", "artifact_id": "12"}]}, {"add": [{"sample_id": "s1", "artifact_id": True}]},
                {"select": "matching"}, {"select": "some"}]:
        r = client.patch(url, json=bad, headers=logged_in)
        assert r.status_code == 400, bad
    assert client.patch(f"/api/aggregations/{aid}/studies/777/samples",
                        json={"select": "all"}, headers=logged_in).status_code == 404
    assert client.patch(url, json={"select": "all"}).status_code == 403   # no CSRF header


def test_route_legacy_study_is_migrated_on_first_open(client, logged_in, stub_qiita):
    """A study saved before per-row selection (rows_v 0, checked samples in aggregation_samples)
    keeps its selection: the first page request turns s2's checked sample into its two rows."""
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    conn_of = stub_qiita.add_study_to_aggregation.__globals__["_conn"]    # the app's own SQLite
    with conn_of() as conn:
        conn.execute("DELETE FROM aggregation_files WHERE aggregation_id=?", (aid,))
        conn.execute("UPDATE aggregation_studies SET rows_v=0, file_rows=NULL WHERE aggregation_id=?", (aid,))
        conn.execute("INSERT INTO aggregation_samples(aggregation_id, study_id, sample_id) VALUES(?,?,?)",
                     (aid, 16326, "s2"))
        conn.commit()
    listed = client.get("/api/aggregations").get_json()["aggregations"]
    study = next(a for a in listed if a["aggregation_id"] == aid)["studies"][0]
    assert (study["selected_rows"], study["file_rows"]) == (1, None)          # legacy: one checked sample
    page = client.get(f"/api/aggregations/{aid}/studies/16326/samples").get_json()
    assert (page["selected_count"], [r["selected"] for r in page["rows"]]) == (2, [True, True, False])
    study = next(a for a in client.get("/api/aggregations").get_json()["aggregations"]
                 if a["aggregation_id"] == aid)["studies"][0]
    assert (study["selected_rows"], study["file_rows"], study["rows_v"]) == (2, 2, 1)


def test_route_export_csv(client, logged_in, stub_qiita):
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    resp = client.get(f"/api/aggregations/{aid}/export.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert resp.headers["Content-Disposition"] == f"attachment; filename=aggregation_{aid}_samples.csv"
    rows = list(csv.reader(resp.get_data(as_text=True).splitlines()))
    assert rows[0] == ["study_id", "sample_id", "artifact_id", "data_type", "processing", "R1", "R2", "barcodes"]
    assert rows[1] == ["16326", "s1", "12", "16S", "Raw upload", "/a/f12_R1.fq.gz", "/a/f12_R2.fq.gz", ""]
    assert len(rows) == 2


def test_route_export_xlsx(client, logged_in, stub_qiita):
    import io
    import openpyxl
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    resp = client.get(f"/api/aggregations/{aid}/export.xlsx")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:200]
    assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert resp.headers["Content-Disposition"] == f"attachment; filename=aggregation_{aid}_samples.xlsx"
    ws = openpyxl.load_workbook(io.BytesIO(resp.get_data())).active
    assert [c.value for c in ws[2]] == [16326, "s1", "12", "16S", "Raw upload", "/a/f12_R1.fq.gz", "/a/f12_R2.fq.gz", None]
    assert ws.cell(row=2, column=2).data_type == "s"
    assert client.get("/api/aggregations/nope/export.xlsx").status_code == 404


def test_route_export_passes_only_checked_rows(client, logged_in, stub_qiita, monkeypatch):
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    client.patch(f"/api/aggregations/{aid}/studies/16326/samples", json={"remove": [_row("s2", 13)]},
                 headers=logged_in)
    seen = {}

    def _capture(selected, file_filters=None):
        seen.update(selected)
        seen["filters"] = file_filters
        return [(16326, "s1", 12, "16S", "Raw upload", "/a/f.fq.gz", "", "")]
    monkeypatch.setattr(stub_qiita, "fetch_export_rows", _capture)
    filt = {"data_types": ["16S"], "processing": ["Raw upload"], "artifacts": ["12"]}
    client.patch(f"/api/aggregations/{aid}/studies/16326", json={"file_filter": filt}, headers=logged_in)
    assert client.get(f"/api/aggregations/{aid}/export.csv").status_code == 200
    assert seen == {16326: {("s2", 12)}, "filters": {16326: filt}}


def test_route_export_errors(client, logged_in, stub_qiita, monkeypatch):
    aid = _create(client, logged_in)["aggregation_id"]
    assert client.get(f"/api/aggregations/{aid}/export.csv").status_code == 400   # no studies
    assert client.get("/api/aggregations/nope/export.csv").status_code == 404
    assert _add(client, logged_in, aid).status_code == 200
    client.patch(f"/api/aggregations/{aid}/studies/16326/samples", json={"select": "none"}, headers=logged_in)
    r = client.get(f"/api/aggregations/{aid}/export.csv")
    assert r.status_code == 400 and r.get_json()["error"] == "No rows selected"

    def _raise(selected, file_filters=None):
        raise ValueError("None of the selected samples has a per-sample sequence file")
    monkeypatch.setattr(stub_qiita, "fetch_export_rows", _raise)
    client.patch(f"/api/aggregations/{aid}/studies/16326/samples", json={"select": "all"}, headers=logged_in)
    resp = client.get(f"/api/aggregations/{aid}/export.csv")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "None of the selected samples has a per-sample sequence file"


def test_route_401_without_session(_app):
    assert _app.test_client().get("/api/aggregations").status_code == 401


def test_route_post_requires_csrf(client, logged_in):
    assert client.post("/api/aggregations", json={"name": "x"}).status_code == 403


# ── TKT-086: a study_detail_cache row without preps is a miss ────────────────

@pytest.mark.parametrize("sid, preps_json", [(70001, None), (70002, "[]")])
def test_study_detail_refetches_when_cached_row_has_no_preps(client, logged_in, monkeypatch, sid, preps_json):
    """Other writers create study_detail_cache rows without preps, and the old
    availability map persisted "[]" into some; the modal must refetch rather
    than serve zero preps as a hit."""
    import routes.study_routes as sr
    sd = sr.study_detail

    # Write through the route's own binding to helpers/study_detail: fresh_db re-imports
    # store per test, but this module-scoped app still holds the original.
    sd.upsert_study_detail_cache(sid, preps_json, preps_json, samples_context="ctx")
    fetched = []
    monkeypatch.setattr(sr, "is_study_public", lambda sid: True)
    monkeypatch.setattr(sd, "_fetch_study_detail_from_qiita",
                        lambda sid: (fetched.append(sid) or ([{"prep_template_id": 7, "data_type": "16S"}], [])))
    monkeypatch.setattr(sd, "fetch_artifact_graph", lambda sid: [])
    monkeypatch.setattr(sd, "_fetch_prep_metadata_summary", lambda pid: {})
    monkeypatch.setattr(sd, "_fetch_study_samples", lambda sid, limit=200: ([], 0))
    monkeypatch.setattr(sd, "_fetch_sample_context_text", lambda sid: "")
    monkeypatch.setattr(sd, "prep_membership", lambda sid: {})
    r = client.get(f"/api/studies/{sid}/detail")
    assert r.status_code == 200, r.get_json()
    assert fetched == [sid]
    assert [p["prep_template_id"] for p in r.get_json()["preps"]] == [7]


def test_route_export_tsv(client, logged_in, stub_qiita, monkeypatch):
    aid = _create(client, logged_in)["aggregation_id"]
    assert client.get(f"/api/aggregations/{aid}/export.tsv").status_code == 400   # no studies
    assert _add(client, logged_in, aid).status_code == 200
    r = client.get(f"/api/aggregations/{aid}/export.tsv")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert r.mimetype == "text/tab-separated-values"
    lines = r.get_data(as_text=True).splitlines()
    assert lines[0].split("\t") == ["study_id", "sample_id", "artifact_id", "data_type", "processing",
                                    "R1", "R2", "barcodes"]
    assert lines[1].split("\t")[:3] == ["16326", "s1", "12"]
