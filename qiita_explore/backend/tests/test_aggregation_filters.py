"""Sample Aggregation routes — the saved file filter (data type / processing /
artifact), Group by prep, Prep ID / Artifact ID sorting and the file facets, with
Qiita Postgres stubbed. Split out of test_aggregations.py (500-line cap); the app,
login and stub fixtures are that module's."""

# ruff: noqa: F811 — the fixtures are imported from test_aggregations (shared
# module-scoped app), so every test parameter "redefines" an imported name.
import pytest  # noqa: F401

from .test_aggregations import _add, _app, _create, _row, client, logged_in, stub_qiita  # noqa: F401


# ── the saved file filter ────────────────────────────────────────────────────

_FILTER_MAP = {"s1": [("Metagenomic", "Atropos v1.1.24", 11, 2, 0)], "s2": [("16S", "Raw upload", 12, 0, 1)]}


def _study(agg, study_id=16326):
    return next(st for st in agg["studies"] if st["study_id"] == study_id)


_EMPTY = {"data_types": [], "processing": [], "artifacts": []}


def test_route_study_file_filter_patch_validates_and_persists(client, logged_in, stub_qiita):
    aid = _create(client, logged_in)["aggregation_id"]
    added = _add(client, logged_in, aid).get_json()
    assert _study(added)["file_filter"] == _EMPTY
    assert "file_filter" not in added                         # no aggregation-level filter any more
    url = f"/api/aggregations/{aid}/studies/16326"
    r = client.patch(url, json={"file_filter": {"data_types": ["16S", "16S"]}}, headers=logged_in)
    assert r.status_code == 200, r.get_json()
    assert _study(r.get_json())["file_filter"] == {"data_types": ["16S"], "processing": [], "artifacts": []}
    listed = [x for x in client.get("/api/aggregations").get_json()["aggregations"] if x["aggregation_id"] == aid]
    assert _study(listed[0])["file_filter"] == {"data_types": ["16S"], "processing": [], "artifacts": []}
    r = client.patch(url, json={"file_filter": {"artifacts": ["140751", "140713"]}}, headers=logged_in)
    assert _study(r.get_json())["file_filter"]["artifacts"] == ["140713", "140751"]
    assert _study(client.patch(url, json={"file_filter": {}}, headers=logged_in).get_json())["file_filter"] == _EMPTY
    for bad in [{}, {"file_filter": []}, {"file_filter": {"other": []}}, {"file_filter": {"data_types": "16S"}},
                {"file_filter": {"data_types": [""]}}, {"file_filter": {"artifacts": [12]}}, {"file_filter": {"processing": ["x" * 201]}},
                {"file_filter": {"data_types": [str(i) for i in range(51)]}}]:
        assert client.patch(url, json=bad, headers=logged_in).status_code == 400, bad
    assert client.patch(f"/api/aggregations/{aid}/studies/777", json={"file_filter": {}}, headers=logged_in).status_code == 404
    assert client.patch("/api/aggregations/nope/studies/16326", json={"file_filter": {}}, headers=logged_in).status_code == 404
    # the aggregation itself no longer takes a filter
    r = client.patch(f"/api/aggregations/{aid}", json={"name": "x", "file_filter": {}}, headers=logged_in)
    assert r.status_code == 400 and "per study" in r.get_json()["error"]


def test_route_study_filters_are_independent(client, logged_in, stub_qiita, monkeypatch):
    """Each study's filter narrows only that study: picking study A's artifact
    (artifacts belong to one study) used to empty study B's table and export."""
    maps = {16326: {"s1": [("Metagenomic", "Raw upload", 11, 2, 0)], "s2": [("16S", "Raw upload", 12, 0, 1)]},
            777: {"s1": [("Metagenomic", "Raw upload", 21, 2, 0)], "s2": [("Metagenomic", "Raw upload", 22, 2, 0)]}}
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: maps[int(sid)])
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    assert _add(client, logged_in, aid, study_id=777).status_code == 200
    r = client.patch(f"/api/aggregations/{aid}/studies/16326", json={"file_filter": {"artifacts": ["11"]}},
                     headers=logged_in)
    assert _study(r.get_json(), 777)["file_filter"] == _EMPTY

    a = client.get(f"/api/aggregations/{aid}/studies/16326/samples").get_json()
    assert ([(x["sample_id"], x["artifact_id"]) for x in a["rows"]], a["with_files"]) == ([("s1", 11)], 1)
    b = client.get(f"/api/aggregations/{aid}/studies/777/samples").get_json()
    assert ([(x["sample_id"], x["artifact_id"]) for x in b["rows"]], b["with_files"]) == ([("s1", 21), ("s2", 22)], 2)
    r = client.patch(f"/api/aggregations/{aid}/studies/777/samples", json={"select": "with_files"}, headers=logged_in)
    assert _study(r.get_json(), 777)["selected_rows"] == 2

    # options per study; the header count adds each study under its own filter
    names = lambda d, k: [o["name"] for o in d[k]]  # noqa: E731
    f777 = client.get(f"/api/aggregations/{aid}/file-facets?study_id=777").get_json()
    assert names(f777, "artifacts") == ["21", "22"] and names(f777, "data_types") == ["Metagenomic"]
    f16 = client.get(f"/api/aggregations/{aid}/file-facets?study_id=16326").get_json()
    assert names(f16, "artifacts") == ["11", "12"]
    header = client.get(f"/api/aggregations/{aid}/file-facets").get_json()
    assert (header["exportable"], header["selected"]) == (1 + 2, 4)
    assert "artifacts" not in header
    assert client.get(f"/api/aggregations/{aid}/file-facets?study_id=555").status_code == 404
    assert client.get(f"/api/aggregations/{aid}/file-facets?study_id=x").status_code == 404

    seen = {}
    monkeypatch.setattr(stub_qiita, "fetch_export_rows",
                        lambda selected, filters: seen.update(filters) or [(777, "s1", 21, "M", "Raw upload", "/r1", "", "")])
    assert client.get(f"/api/aggregations/{aid}/export.csv").status_code == 200
    assert seen == {16326: {"data_types": [], "processing": [], "artifacts": ["11"]}, 777: _EMPTY}


def test_route_samples_and_select_follow_file_filter(client, logged_in, stub_qiita, monkeypatch):
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: _FILTER_MAP)
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    base = f"/api/aggregations/{aid}/studies/16326/samples"

    page = client.get(base).get_json()               # no filter: both have files
    assert page["with_files"] == 2
    client.patch(f"/api/aggregations/{aid}/studies/16326", json={"file_filter": {"data_types": ["16S"]}}, headers=logged_in)
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
    assert r.get_json()["studies"][0]["selected_rows"] == 1


def test_route_samples_follow_artifact_filter(client, logged_in, stub_qiita, monkeypatch):
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: _FILTER_MAP)
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    base = f"/api/aggregations/{aid}/studies/16326/samples"
    client.patch(f"/api/aggregations/{aid}/studies/16326", json={"file_filter": {"artifacts": ["11"]}}, headers=logged_in)
    page = client.get(base).get_json()
    # Only s1 has a file in artifact 11; s2 (artifact 12) is out of scope, not just file-less.
    assert [(r["sample_id"], r["artifact_id"]) for r in page["rows"]] == [("s1", 11)]
    assert (page["total"], page["with_files"]) == (1, 1)
    r = client.patch(base, json={"select": "with_files"}, headers=logged_in)
    assert r.get_json()["studies"][0]["selected_rows"] == 1


def _prep_stubs(monkeypatch, ar, membership):
    monkeypatch.setattr(ar, "prep_membership", lambda sid: membership)
    monkeypatch.setattr(ar, "prep_data_types", lambda sid: {k: sorted({dt for _p, dt in v})
                                                            for k, v in membership.items()})


def _keys(page):
    return [(r["sample_id"], r["artifact_id"]) for r in page["rows"]]


def test_route_rows_carry_their_artifacts_prep_and_group_by_prep(client, logged_in, stub_qiita, monkeypatch):
    # s2 is in artifact 12 (prep 5) and 13 (prep 6); s1 has no file but is a member of both preps.
    _prep_stubs(monkeypatch, stub_qiita, {"s1": [(5, "16S"), (6, "WGS")], "s2": [(5, "16S"), (6, "WGS")]})
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    base = f"/api/aggregations/{aid}/studies/16326/samples"

    flat = client.get(base).get_json()
    # each file row shows only ITS artifact's prep, not the sample's whole membership; the
    # file-less placeholder shows every prep the sample is in
    assert [(r["sample_id"], r["artifact_id"], r["prep_id"], r["prep_ids"]) for r in flat["rows"]] == [
        ("s2", 12, 5, [5]), ("s2", 13, 6, [6]), ("s1", None, None, [5, 6])]
    assert "groups" not in flat

    page = client.get(base + "?group=prep").get_json()
    # prep 5: s2's artifact-12 row, then s1's placeholder; prep 6 likewise — the placeholder sits under each member prep
    assert [(r["prep_id"], r["sample_id"], r["artifact_id"]) for r in page["rows"]] == [
        (5, "s2", 12), (5, "s1", None), (6, "s2", 13), (6, "s1", None)]
    assert page["total"] == 4
    assert page["groups"] == [{"prep_id": 5, "data_type": "16S", "count": 2},
                              {"prep_id": 6, "data_type": "WGS", "count": 2}]
    # paging works over rows
    second = client.get(base + "?group=prep&offset=2&limit=1").get_json()
    assert [(r["prep_id"], r["artifact_id"]) for r in second["rows"]] == [(6, 13)]
    assert client.get(base + "?group=sample").status_code == 400


def test_route_group_by_prep_honours_data_type_filter_and_puts_no_prep_last(client, logged_in, stub_qiita, monkeypatch):
    _prep_stubs(monkeypatch, stub_qiita, {"s1": [(5, "16S"), (6, "WGS")]})   # s1 has no file
    monkeypatch.setattr(stub_qiita, "artifact_preps", lambda sid: {12: 5})   # artifact 13 has no known prep
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    base = f"/api/aggregations/{aid}/studies/16326/samples?group=prep"
    page = client.get(base).get_json()
    assert [(r["prep_id"], r["sample_id"], r["artifact_id"]) for r in page["rows"]] == [
        (5, "s2", 12), (5, "s1", None), (6, "s1", None), (None, "s2", 13)]
    assert page["groups"][-1] == {"prep_id": None, "data_type": None, "count": 1}
    client.patch(f"/api/aggregations/{aid}/studies/16326", json={"file_filter": {"data_types": ["WGS"]}}, headers=logged_in)
    page = client.get(base).get_json()
    # s1 only under its WGS prep; s2 (16S files, no prep membership) is out of scope under a WGS filter.
    assert [(r["prep_id"], r["sample_id"]) for r in page["rows"]] == [(6, "s1")]


def _sort_setup(client, logged_in, stub_qiita, monkeypatch, preps=None):
    # Rows: s1/30 (prep 5), s2/20 (prep 9), s2/40 (prep 7), s3 placeholder (no file, no prep).
    # No Data type filter, so every sample is in scope.
    _prep_stubs(monkeypatch, stub_qiita, {})
    monkeypatch.setattr(stub_qiita, "artifact_preps", lambda sid: preps or {30: 5, 20: 9, 40: 7})
    monkeypatch.setattr(stub_qiita, "list_study_sample_ids", lambda sid: ["s1", "s2", "s3"])
    monkeypatch.setattr(stub_qiita, "fetch_samples_by_ids", lambda sid, ids: [(i, "x") for i in ids])
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: {
        "s1": [("16S", "Raw upload", 30, 1, 0)],
        "s2": [("16S", "Raw upload", 20, 2, 0), ("16S", "Raw upload", 40, 1, 0)]})
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    return aid, f"/api/aggregations/{aid}/studies/16326/samples"


def test_route_sort_by_prep_and_artifact(client, logged_in, stub_qiita, monkeypatch):
    aid, base = _sort_setup(client, logged_in, stub_qiita, monkeypatch)
    # default: files-first sample order, a sample's rows by artifact
    assert _keys(client.get(base).get_json()) == [("s1", 30), ("s2", 20), ("s2", 40), ("s3", None)]
    # prep: each ROW's own prep id (30 -> 5, 40 -> 7, 20 -> 9); the row with none last both ways
    assert _keys(client.get(base + "?sort=prep").get_json()) == [("s1", 30), ("s2", 40), ("s2", 20), ("s3", None)]
    assert _keys(client.get(base + "?sort=prep&dir=desc").get_json()) == [("s2", 20), ("s2", 40), ("s1", 30), ("s3", None)]
    # artifact: each row's artifact id
    assert _keys(client.get(base + "?sort=artifact&dir=asc").get_json()) == [("s2", 20), ("s1", 30), ("s2", 40), ("s3", None)]
    assert _keys(client.get(base + "?sort=artifact&dir=desc").get_json()) == [("s2", 40), ("s1", 30), ("s2", 20), ("s3", None)]
    # an Artifact filter keeps only its artifacts' rows, and (as before) drops samples with no file in them
    client.patch(f"/api/aggregations/{aid}/studies/16326", json={"file_filter": {"artifacts": ["30", "40"]}}, headers=logged_in)
    assert _keys(client.get(base + "?sort=artifact").get_json()) == [("s1", 30), ("s2", 40)]
    for bad in ("?sort=bogus", "?sort=prep&dir=up"):
        assert client.get(base + bad).status_code == 400
    assert _keys(client.get(base + "?dir=desc").get_json()) == [("s1", 30), ("s2", 40)]   # dir without sort is ignored
    assert client.get(base + "?dir=bogus").status_code == 200


def test_route_sort_ties_keep_files_first_then_id(client, logged_in, stub_qiita, monkeypatch):
    aid, base = _sort_setup(client, logged_in, stub_qiita, monkeypatch, preps={30: 5, 20: 5, 40: 5})
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: {"s2": [("16S", "Raw upload", 20, 2, 0)]})
    # Sort by prep with every file row in prep 5 (the placeholders have none and sort last): the
    # files-first / id order of the tied rows stays.
    assert _keys(client.get(base + "?sort=prep").get_json()) == [("s2", 20), ("s1", None), ("s3", None)]


def test_route_sort_grouped(client, logged_in, stub_qiita, monkeypatch):
    aid, base = _sort_setup(client, logged_in, stub_qiita, monkeypatch)
    rows = lambda page: [(r["prep_id"], r["sample_id"], r["artifact_id"]) for r in page["rows"]]   # noqa: E731
    g = base + "?group=prep"
    assert rows(client.get(g).get_json()) == [(5, "s1", 30), (7, "s2", 40), (9, "s2", 20), (None, "s3", None)]
    # sort=prep&dir=desc reverses the groups; "No prep" stays last
    assert rows(client.get(g + "&sort=prep&dir=desc").get_json()) == [
        (9, "s2", 20), (7, "s2", 40), (5, "s1", 30), (None, "s3", None)]
    # sort=artifact orders rows inside a group while the groups stay ascending
    monkeypatch.setattr(stub_qiita, "artifact_preps", lambda sid: {30: 5, 20: 5, 40: 5})
    assert rows(client.get(g + "&sort=artifact&dir=asc").get_json()) == [
        (5, "s2", 20), (5, "s1", 30), (5, "s2", 40), (None, "s3", None)]
    assert rows(client.get(g + "&sort=artifact&dir=desc").get_json()) == [
        (5, "s2", 40), (5, "s1", 30), (5, "s2", 20), (None, "s3", None)]


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

    client.patch(f"/api/aggregations/{aid}/studies/16326", json={"file_filter": {"data_types": ["16S"]}}, headers=logged_in)
    page = client.get(base).get_json()
    assert [r["sample_id"] for r in page["rows"]] == ["s1"]
    assert (page["total"], page["with_files"]) == (1, 0)


def test_route_file_facets_includes_prep_only_type(client, logged_in, stub_qiita, monkeypatch):
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: {})
    monkeypatch.setattr(stub_qiita, "prep_data_types", lambda sid: {"s1": ["16S"], "s2": ["18S"]})
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    d = client.get(f"/api/aggregations/{aid}/file-facets?study_id=16326").get_json()
    assert d["data_types"] == [{"name": "16S", "count": 1}, {"name": "18S", "count": 1}]
    assert d["exportable"] == 0


def test_route_file_facets(client, logged_in, stub_qiita, monkeypatch):
    monkeypatch.setattr(stub_qiita, "get_sample_files", lambda sid: _FILTER_MAP)
    aid = _create(client, logged_in)["aggregation_id"]
    assert _add(client, logged_in, aid).status_code == 200
    d = client.get(f"/api/aggregations/{aid}/file-facets?study_id=16326").get_json()
    assert d["data_types"] == [{"name": "16S", "count": 1}, {"name": "Metagenomic", "count": 1}]
    assert d["processing"] == [{"name": "Atropos v1.1.24", "count": 1}, {"name": "Raw upload", "count": 1}]
    assert d["artifacts"] == [{"name": "11", "count": 1}, {"name": "12", "count": 1}]
    assert (d["exportable"], d["selected"]) == (2, 2)

    client.patch(f"/api/aggregations/{aid}/studies/16326", json={"file_filter": {"data_types": ["Metagenomic"]}},
                 headers=logged_in)
    client.patch(f"/api/aggregations/{aid}/studies/16326/samples", json={"remove": [_row("s1", 11)]}, headers=logged_in)
    d = client.get(f"/api/aggregations/{aid}/file-facets?study_id=16326").get_json()
    assert d["processing"] == [{"name": "Atropos v1.1.24", "count": 1}]  # narrowed by the data-type pick
    assert (d["exportable"], d["selected"]) == (0, 1)  # (s2, 12) is checked but isn't a metagenomic row
    assert client.get("/api/aggregations/nope/file-facets").status_code == 404
