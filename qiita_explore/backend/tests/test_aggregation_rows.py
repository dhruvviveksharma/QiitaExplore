"""helpers/aggregation_rows: the row model of the Sample Aggregation table — one row
per (sample, artifact), the artifact fixing the prep; placeholders for file-less
samples; grouping and sorting. Pure Python, no Postgres."""

import pytest

from .conftest import stub_qiita_db_and_core

RAW = "Raw upload"
NONE = {"data_types": [], "processing": [], "artifacts": []}


@pytest.fixture
def ar():
    stub_qiita_db_and_core()
    import helpers.aggregation_rows as mod
    return mod


def _e(dt, aid, proc=RAW):
    return (dt, proc, aid, 1, 0)


# The user's case: 10317.000001299 is in preps 1115 + 1134; artifact 2947 is prep 1115's, 2247 prep 1134's.
FILES = {"a": [_e("16S", 2947), _e("16S", 2247)], "b": [_e("16S", 2947)]}
MEMBER = {"a": [(1115, "16S"), (1134, "16S")], "b": [(1115, "16S")], "c": [(1134, "16S")]}
ART_PREP = {2947: 1115, 2247: 1134}


def order(ar, ids=("a", "b", "c", "d"), flt=NONE, group=None, sort=None, desc=False, files=FILES, member=MEMBER):
    return ar.order_rows(list(ids), files, member, ART_PREP, flt, group, sort, desc)


def test_a_sample_in_two_artifacts_is_two_rows_each_with_its_own_prep(ar):
    keys, groups = order(ar, ids=["a"])
    assert keys == [("a", 2247, 1134), ("a", 2947, 1115)]          # by artifact id; prep from the artifact
    assert groups is None


def test_file_less_sample_is_one_placeholder_row(ar):
    keys, _ = order(ar, ids=["c", "d"])
    assert keys == [("c", None, None), ("d", None, None)]          # ungrouped: no prep on a placeholder


def test_filter_drops_file_rows_and_turns_the_sample_into_a_placeholder(ar):
    keys, _ = order(ar, ids=["a"], flt={"data_types": [], "processing": [], "artifacts": ["2947"]})
    assert keys == [("a", 2947, 1115)]
    keys, _ = order(ar, ids=["a"], flt={"data_types": ["WGS"], "processing": [], "artifacts": []})
    assert keys == [("a", None, None)]


def test_grouped_clusters_by_prep_and_counts_rows(ar):
    keys, groups = order(ar, group="prep")
    assert keys == [("a", 2947, 1115), ("b", 2947, 1115),
                    ("a", 2247, 1134), ("c", None, 1134),          # c: placeholder under its member prep
                    ("d", None, None)]                             # no prep at all -> "No prep" last
    assert groups == [{"prep_id": 1115, "data_type": "16S", "count": 2},
                      {"prep_id": 1134, "data_type": "16S", "count": 2},
                      {"prep_id": None, "data_type": None, "count": 1}]


def test_grouped_placeholder_follows_the_data_type_filter(ar):
    member = {"c": [(5, "16S"), (6, "WGS")]}
    keys, _ = order(ar, ids=["c"], group="prep", flt={"data_types": ["WGS"], "processing": [], "artifacts": []},
                    files={}, member=member)
    assert keys == [("c", None, 6)]


def test_sort_by_artifact_and_prep_ascending_and_descending(ar):
    ids = ["a", "b"]
    assert order(ar, ids, sort="artifact")[0] == [("a", 2247, 1134), ("a", 2947, 1115), ("b", 2947, 1115)]
    assert order(ar, ids, sort="artifact", desc=True)[0] == [("a", 2947, 1115), ("b", 2947, 1115), ("a", 2247, 1134)]
    assert order(ar, ids, sort="prep")[0] == [("a", 2947, 1115), ("b", 2947, 1115), ("a", 2247, 1134)]
    assert order(ar, ids, sort="prep", desc=True)[0] == [("a", 2247, 1134), ("a", 2947, 1115), ("b", 2947, 1115)]


def test_rows_with_no_value_sort_last_in_both_directions(ar):
    for desc in (False, True):
        assert order(ar, ["c", "a"], sort="artifact", desc=desc)[0][-1] == ("c", None, None)
        assert order(ar, ["c", "a"], sort="prep", desc=desc)[0][-1] == ("c", None, None)


def test_grouped_sort_prep_flips_groups_but_artifact_sorts_inside_them(ar):
    keys, groups = order(ar, ids=["a", "b"], group="prep", sort="prep", desc=True)
    assert [g["prep_id"] for g in groups] == [1134, 1115]
    assert [k[2] for k in keys] == [1134, 1115, 1115]
    files = {"a": [_e("16S", 2947), _e("16S", 2248)], "b": [_e("16S", 2949)]}
    art_prep = {2947: 1115, 2248: 1115, 2949: 1115}
    keys, groups = ar.order_rows(["a", "b"], files, MEMBER, art_prep, NONE, "prep", "artifact", True)
    assert keys == [("b", 2949, 1115), ("a", 2947, 1115), ("a", 2248, 1115)]     # groups stay ascending
    assert [g["prep_id"] for g in groups] == [1115]


def test_ties_keep_the_incoming_order(ar):
    files = {"x": [_e("16S", 2947)], "y": [_e("16S", 2947)]}
    keys, _ = ar.order_rows(["y", "x"], files, {}, ART_PREP, NONE, None, "artifact", False)
    assert [k[0] for k in keys] == ["y", "x"]


def test_file_entries_sorted_and_filtered(ar):
    assert [e[2] for e in ar.file_entries(FILES, "a", NONE)] == [2247, 2947]
    assert ar.file_entries(FILES, "zz", NONE) == []
    assert [e[2] for e in ar.file_entries(FILES, "a", {"artifacts": ["2947"]})] == [2947]
