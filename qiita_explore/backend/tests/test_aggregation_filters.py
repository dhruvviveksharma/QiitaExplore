"""Sample Aggregation routes — the saved file filter (data type / processing /
artifact), Group by prep, Prep ID / Artifact ID sorting and the file facets, with
Qiita Postgres stubbed. Split out of test_aggregations.py (500-line cap); the app,
login and stub fixtures are that module's."""

import pytest  # noqa: F401

from .test_aggregations import _add, _app, _create, client, logged_in, stub_qiita  # noqa: F401


# ── the saved file filter ────────────────────────────────────────────────────

_FILTER_MAP = {"s1": [("Metagenomic", "Atropos v1.1.24", 11, 2, 0)], "s2": [("16S", "Raw upload", 12, 0, 1)]}


def test_route_file_filter_patch_validates_and_persists(client, logged_in, stub_qiita):
    aid = _create(client, logged_in)["aggregation_id"]
    assert client.get("/api/aggregations").get_json()["aggregations"][0]["file_filter"] == \
        {"data_types": [], "processing": [], "artifacts": []}
    url = f"/api/aggregations/{aid}"
    r = client.patch(url, json={"file_filter": {"data_types": ["16S", "16S"]}}, headers=logged_in)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["file_filter"] == {"data_types": ["16S"], "processing": [], "artifacts": []}
    listed = [a for a in client.get("/api/aggregations").get_json()["aggregations"] if a["aggregation_id"] == aid]
    assert listed[0]["file_filter"] == {"data_types": ["16S"], "processing": [], "artifacts": []}
    r = client.patch(url, json={"file_filter": {"artifacts": ["140751", "140713"]}}, headers=logged_in)
    assert r.get_json()["file_filter"]["artifacts"] == ["140713", "140751"]
    r = client.patch(url, json={"name": "Both", "file_filter": {}}, headers=logged_in)
    assert (r.get_json()["name"], r.get_json()["file_filter"]) == \
        ("Both", {"data_types": [], "processing": [], "artifacts": []})
    for bad in [{}, {"file_filter": []}, {"file_filter": {"other": []}}, {"file_filter": {"data_types": "16S"}},
                {"file_filter": {"data_types": [""]}}, {"file_filter": {"artifacts": [12]}}, {"file_filter": {"processing": ["x" * 201]}},
                {"file_filter": {"data_types": [str(i) for i in range(51)]}}]:
        assert client.patch(url, json=bad, headers=logged_in).status_code == 400, bad
    assert client.patch("/api/aggregations/nope", json={"file_filter": {}}, headers=logged_in).status_code == 404


def test_route_samples_and_select_follow_file_filter(client, logged_in, stub_qiita, monkeypatch):
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: _FILTER_MAP)
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    base = f"/api/aggregations/{aid}/studies/16326/samples"

    page = client.get(base).get_json()               # no filter: both have files
    assert page["with_files"] == 2
    client.patch(f"/api/aggregations/{aid}", json={"file_filter": {"data_types": ["16S"]}}, headers=logged_in)
    page = client.get(base).get_json()
    # s1's only data type (Metagenomic, from its file) doesn't match the 16S
    # filter, and it has no prep membership (stubbed empty), so it drops out
    # of scope entirely rather than being kept and shown with a dimmed file.
    assert (page["total"], page["with_files"]) == (1, 1)
    assert [r["sample_id"] for r in page["rows"]] == ["s2"]
    s2 = page["rows"][0]
    assert (s2["fastq"], s2["fasta"]) == (None, True)
    assert client.get(base + "?show=without_files").get_json()["rows"] == []

    r = client.patch(base, json={"select": "with_files"}, headers=logged_in)
    assert r.get_json()["studies"][0]["selected_samples"] == 1


def test_route_samples_follow_artifact_filter(client, logged_in, stub_qiita, monkeypatch):
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: _FILTER_MAP)
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    base = f"/api/aggregations/{aid}/studies/16326/samples"
    client.patch(f"/api/aggregations/{aid}", json={"file_filter": {"artifacts": ["11"]}}, headers=logged_in)
    page = client.get(base).get_json()
    # Only s1 has a file in artifact 11; s2 (artifact 12) is out of scope, not just file-less.
    assert [r["sample_id"] for r in page["rows"]] == ["s1"]
    assert (page["total"], page["with_files"]) == (1, 1)
    r = client.patch(base, json={"select": "with_files"}, headers=logged_in)
    assert r.get_json()["studies"][0]["selected_samples"] == 1


def _prep_stubs(monkeypatch, ar, membership):
    monkeypatch.setattr(ar, "prep_membership", lambda sid: membership)
    monkeypatch.setattr(ar, "prep_data_types", lambda sid: {k: sorted({dt for _p, dt in v})
                                                            for k, v in membership.items()})


def test_route_samples_carry_prep_ids_and_group_by_prep(client, logged_in, stub_qiita, monkeypatch):
    # s1 is in both preps; s2 (the only sample with a file) only in prep 6.
    _prep_stubs(monkeypatch, stub_qiita, {"s1": [(5, "16S"), (6, "WGS")], "s2": [(6, "WGS")]})
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    base = f"/api/aggregations/{aid}/studies/16326/samples"

    flat = client.get(base).get_json()
    assert [(r["sample_id"], r["prep_ids"]) for r in flat["rows"]] == [("s2", [6]), ("s1", [5, 6])]
    assert "groups" not in flat and "prep_id" not in flat["rows"][0]

    page = client.get(base + "?group=prep").get_json()
    # prep 5: s1; prep 6: s2 (has a file, so first) then s1 — s1 sits under both preps.
    assert [(r["prep_id"], r["sample_id"]) for r in page["rows"]] == [(5, "s1"), (6, "s2"), (6, "s1")]
    assert page["total"] == 3
    assert page["groups"] == [{"prep_id": 5, "data_type": "16S", "count": 1},
                              {"prep_id": 6, "data_type": "WGS", "count": 2}]
    # paging works over (sample, prep) rows
    second = client.get(base + "?group=prep&offset=2&limit=1").get_json()
    assert [(r["prep_id"], r["sample_id"]) for r in second["rows"]] == [(6, "s1")]
    assert client.get(base + "?group=sample").status_code == 400


def test_route_group_by_prep_honours_data_type_filter_and_puts_no_prep_last(client, logged_in, stub_qiita, monkeypatch):
    _prep_stubs(monkeypatch, stub_qiita, {"s1": [(5, "16S"), (6, "WGS")]})   # s2 is in no prep
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    base = f"/api/aggregations/{aid}/studies/16326/samples?group=prep"
    page = client.get(base).get_json()
    assert [(r["prep_id"], r["sample_id"]) for r in page["rows"]] == [(5, "s1"), (6, "s1"), (None, "s2")]
    assert page["groups"][-1] == {"prep_id": None, "data_type": None, "count": 1}
    client.patch(f"/api/aggregations/{aid}", json={"file_filter": {"data_types": ["WGS"]}}, headers=logged_in)
    page = client.get(base).get_json()
    # s1 only under its WGS prep; s2 (16S file, no prep) is out of scope under a WGS filter.
    assert [(r["prep_id"], r["sample_id"]) for r in page["rows"]] == [(6, "s1")]


def _ids(page):
    return [r["sample_id"] for r in page["rows"]]


def _sort_setup(client, logged_in, stub_qiita, monkeypatch):
    # s1: preps 5+9, artifact 30 (file); s2: prep 7, artifacts 20+40; s3: no prep, no file.
    # No Data type filter, so every sample is in scope.
    members = {"s1": [(5, "16S"), (9, "16S")], "s2": [(7, "16S")]}
    _prep_stubs(monkeypatch, stub_qiita, members)
    monkeypatch.setattr(stub_qiita, "list_study_sample_ids", lambda sid: ["s1", "s2", "s3"])
    monkeypatch.setattr(stub_qiita, "fetch_samples_by_ids",
                        lambda sid, ids: [(i, "x") for i in ids])
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: {
        "s1": [("16S", "Raw upload", 30, 1, 0)],
        "s2": [("16S", "Raw upload", 20, 2, 0), ("16S", "Raw upload", 40, 1, 0)]})
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    return aid, f"/api/aggregations/{aid}/studies/16326/samples"


def test_route_sort_by_prep_and_artifact(client, logged_in, stub_qiita, monkeypatch):
    aid, base = _sort_setup(client, logged_in, stub_qiita, monkeypatch)
    assert _ids(client.get(base).get_json()) == ["s1", "s2", "s3"]                    # default: files-first / id
    # prep: lowest id ascending (s1 -> 5, s2 -> 7), highest descending (s1 -> 9, s2 -> 7); no prep last both ways
    assert _ids(client.get(base + "?sort=prep").get_json()) == ["s1", "s2", "s3"]
    assert _ids(client.get(base + "?sort=prep&dir=desc").get_json()) == ["s1", "s2", "s3"]
    # artifact: lowest asc (s2 -> 20, s1 -> 30), highest desc (s2 -> 40, s1 -> 30); no artifact last
    assert _ids(client.get(base + "?sort=artifact&dir=asc").get_json()) == ["s2", "s1", "s3"]
    assert _ids(client.get(base + "?sort=artifact&dir=desc").get_json()) == ["s2", "s1", "s3"]
    # an Artifact filter only counts the artifacts that pass it: s2 restricted to 40, so s1 (30) is first
    client.patch(f"/api/aggregations/{aid}", json={"file_filter": {"artifacts": ["30", "40"]}}, headers=logged_in)
    assert _ids(client.get(base + "?sort=artifact").get_json()) == ["s1", "s2"]
    for bad in ("?sort=bogus", "?sort=prep&dir=up"):
        assert client.get(base + bad).status_code == 400
    assert _ids(client.get(base + "?dir=desc").get_json()) == ["s1", "s2"]            # dir without sort is ignored
    assert client.get(base + "?dir=bogus").status_code == 200


def test_route_sort_ties_keep_files_first_then_id(client, logged_in, stub_qiita, monkeypatch):
    aid, base = _sort_setup(client, logged_in, stub_qiita, monkeypatch)
    # Sort by prep with every sample in the same prep: nothing to separate them, so the default order stays.
    _prep_stubs(monkeypatch, stub_qiita, {"s1": [(5, "16S")], "s2": [(5, "16S")], "s3": [(5, "16S")]})
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: {"s2": [("16S", "Raw upload", 20, 2, 0)]})
    assert _ids(client.get(base + "?sort=prep").get_json()) == ["s2", "s1", "s3"]


def test_route_sort_grouped(client, logged_in, stub_qiita, monkeypatch):
    aid, base = _sort_setup(client, logged_in, stub_qiita, monkeypatch)
    pairs = lambda page: [(r["prep_id"], r["sample_id"]) for r in page["rows"]]       # noqa: E731
    g = base + "?group=prep"
    assert pairs(client.get(g).get_json()) == [(5, "s1"), (7, "s2"), (9, "s1"), (None, "s3")]
    # sort=prep&dir=desc reverses the groups; "No prep" stays last
    assert pairs(client.get(g + "&sort=prep&dir=desc").get_json()) == [(9, "s1"), (7, "s2"), (5, "s1"), (None, "s3")]
    # sort=artifact orders rows inside a group while the groups stay ascending
    monkeypatch.setattr(stub_qiita, "prep_membership", lambda sid: {"s1": [(5, "16S")], "s2": [(5, "16S")]})
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: {
        "s1": [("16S", "Raw upload", 30, 1, 0)], "s2": [("16S", "Raw upload", 20, 2, 0)]})
    assert pairs(client.get(g + "&sort=artifact&dir=asc").get_json()) == [(5, "s2"), (5, "s1"), (None, "s3")]
    assert pairs(client.get(g + "&sort=artifact&dir=desc").get_json()) == [(5, "s1"), (5, "s2"), (None, "s3")]


def test_route_samples_scoped_by_prep_data_type(client, logged_in, stub_qiita, monkeypatch):
    """Studies like 1070 / 1889 (16S / 18S) have no per-sample sequence file
    at all — only prep membership says what data type a sample belongs to.
    Those samples must still be in scope, shown with an empty file_data_types
    ("—" in the UI), and narrowed by the Data type filter same as a file
    would be."""
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: {})
    _prep_stubs(monkeypatch, stub_qiita, {"s1": [(5, "16S")], "s2": [(6, "18S")]})
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    base = f"/api/aggregations/{aid}/studies/16326/samples"

    page = client.get(base).get_json()
    assert (page["total"], page["with_files"]) == (2, 0)
    row = next(r for r in page["rows"] if r["sample_id"] == "s1")
    assert (row["fastq"], row["fasta"], row["data_types"], row["file_data_types"]) == \
        (None, False, ["16S"], [])

    client.patch(f"/api/aggregations/{aid}", json={"file_filter": {"data_types": ["16S"]}}, headers=logged_in)
    page = client.get(base).get_json()
    assert [r["sample_id"] for r in page["rows"]] == ["s1"]
    assert (page["total"], page["with_files"]) == (1, 0)


def test_route_file_facets_includes_prep_only_type(client, logged_in, stub_qiita, monkeypatch):
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: {})
    monkeypatch.setattr(stub_qiita, "prep_data_types", lambda sid: {"s1": ["16S"], "s2": ["18S"]})
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    d = client.get(f"/api/aggregations/{aid}/file-facets").get_json()
    assert d["data_types"] == [{"name": "16S", "count": 1}, {"name": "18S", "count": 1}]
    assert d["exportable"] == 0


def test_route_file_facets(client, logged_in, stub_qiita, monkeypatch):
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: _FILTER_MAP)
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    d = client.get(f"/api/aggregations/{aid}/file-facets").get_json()
    assert d["data_types"] == [{"name": "16S", "count": 1}, {"name": "Metagenomic", "count": 1}]
    assert d["processing"] == [{"name": "Atropos v1.1.24", "count": 1}, {"name": "Raw upload", "count": 1}]
    assert d["artifacts"] == [{"name": "11", "count": 1}, {"name": "12", "count": 1}]
    assert (d["exportable"], d["selected"]) == (2, 2)

    client.patch(f"/api/aggregations/{aid}", json={"file_filter": {"data_types": ["Metagenomic"]}},
                 headers=logged_in)
    client.patch(f"/api/aggregations/{aid}/studies/16326/samples", json={"remove": ["s1"]}, headers=logged_in)
    d = client.get(f"/api/aggregations/{aid}/file-facets").get_json()
    assert d["processing"] == [{"name": "Atropos v1.1.24", "count": 1}]  # narrowed by the data-type pick
    assert (d["exportable"], d["selected"]) == (0, 1)  # s2 is checked but has no metagenomic file
    assert client.get("/api/aggregations/nope/file-facets").status_code == 404
