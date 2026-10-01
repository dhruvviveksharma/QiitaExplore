"""helpers/study_samples: SQL shape and bind order against a mocked pool (no
Postgres). The module object is imported per test and patched in place, since
module-scoped app fixtures elsewhere purge and re-import helpers.*."""

from unittest.mock import patch

import pytest

from .conftest import stub_qiita_db_and_core

SENT = "qiita_sample_column_names"


@pytest.fixture
def ss():
    stub_qiita_db_and_core()
    import helpers.study_samples as ss
    ss._columns_cache.clear()
    ss._prep_dt_cache.clear()
    ss._artifact_prep_cache.clear()
    return ss


def test_list_ids_excludes_sentinel_and_binds_it(ss):
    with patch.object(ss, "pooled_fetchall", return_value=[("a",), ("b",)]) as m:
        assert ss.list_study_sample_ids("232") == ["a", "b"]
    sql, params = m.call_args[0]
    assert "FROM qiita.sample_232 WHERE sample_id <> %s ORDER BY sample_id" in sql
    assert params == [SENT]


def test_display_columns_prefers_caps_at_four_and_caches(ss):
    cols = ["description", "empo_3", "sample_type", "host_body_site", "env_material", "other"]
    with patch.object(ss, "pooled_fetchall", return_value=[(cols,)]) as m:
        want = ["sample_type", "host_body_site", "env_material", "empo_3"]
        assert ss.display_columns(232) == want
        assert ss.display_columns("232") == want     # served from the memo
        assert m.call_count == 1
        assert m.call_args[0][1] == [SENT]
        assert "sample_values->'columns'" in m.call_args[0][0]


def test_display_columns_without_sentinel_row(ss):
    with patch.object(ss, "pooled_fetchall", return_value=[]):
        assert ss.display_columns(5) == []


def test_fetch_samples_by_ids_sql_and_bind_order(ss):
    ss._columns_cache[232] = (1e18, ["sample_type", "empo_3"])
    with patch.object(ss, "pooled_fetchall", return_value=[("s1", "stool", "Animal")]) as m:
        rows = ss.fetch_samples_by_ids(232, ["s1"])
    assert rows == [("s1", "stool", "Animal")]
    sql, params = m.call_args[0]
    assert "SELECT sample_id, sample_values->>%s, sample_values->>%s FROM qiita.sample_232" in sql
    assert sql.rstrip().endswith("WHERE sample_id = ANY(%s)")
    # select-list column names -> the id list
    assert params == ["sample_type", "empo_3", ["s1"]]


def test_fetch_samples_by_ids_reorders_to_input_order(ss):
    ss._columns_cache[232] = (1e18, [])
    with patch.object(ss, "pooled_fetchall", return_value=[("s2",), ("s1",)]):
        rows = ss.fetch_samples_by_ids(232, ["s1", "s2"])
    assert [r[0] for r in rows] == ["s1", "s2"]


def test_fetch_samples_by_ids_empty_input_no_query(ss):
    with patch.object(ss, "pooled_fetchall") as m:
        assert ss.fetch_samples_by_ids(232, []) == []
    assert not m.called


def test_matching_sample_ids(ss):
    with patch.object(ss, "pooled_fetchall", return_value=[("s2",)]) as m:
        assert ss.matching_sample_ids(232, " skin ") == ["s2"]
    sql, params = m.call_args[0]
    assert "ILIKE" in sql
    assert params == [SENT, "%skin%", "%skin%"]


def test_prep_data_types_groups_and_binds(ss):
    rows = [("s1", 5, "16S"), ("s2", 5, "16S"), ("s1", 6, "18S")]  # s1 is in two preps' data types
    with patch.object(ss, "pooled_fetchall", return_value=rows) as m:
        assert ss.prep_data_types(232) == {"s1": ["16S", "18S"], "s2": ["16S"]}
    sql, params = m.call_args[0]
    assert "study_prep_template" in sql and "prep_template_sample" in sql
    assert params == [232]


def test_prep_data_types_caches(ss):
    with patch.object(ss, "pooled_fetchall", return_value=[("s1", 5, "16S")]) as m:
        assert ss.prep_data_types(232) == {"s1": ["16S"]}
        assert ss.prep_data_types("232") == {"s1": ["16S"]}  # served from the memo
    assert m.call_count == 1


def test_prep_data_types_empty(ss):
    with patch.object(ss, "pooled_fetchall", return_value=[]):
        assert ss.prep_data_types(5) == {}


# ── prep_membership / prep_groups / fetch_prep_samples ───────────────────────

ROWS = [("s2", 6, "WGS"), ("s1", 6, "WGS"), ("s1", 5, "16S"), ("s3", 5, "16S")]


def test_prep_membership_sorted_by_prep_and_shares_the_memo(ss):
    with patch.object(ss, "pooled_fetchall", return_value=ROWS) as m:
        assert ss.prep_membership(232) == {"s1": [(5, "16S"), (6, "WGS")], "s2": [(6, "WGS")], "s3": [(5, "16S")]}
        assert ss.prep_data_types(232) == {"s1": ["16S", "WGS"], "s2": ["WGS"], "s3": ["16S"]}
    assert m.call_count == 1


def test_prep_groups_counts_and_no_prep_remainder(ss):
    def fake(sql, params=None):
        return [(r,) for r in ("s1", "s2", "s3", "s4")] if "ORDER BY sample_id" in sql else ROWS
    with patch.object(ss, "pooled_fetchall", side_effect=fake):
        assert ss.prep_groups(232) == [
            {"prep_id": 5, "data_type": "16S", "num_samples": 2},
            {"prep_id": 6, "data_type": "WGS", "num_samples": 2},
            {"prep_id": None, "data_type": None, "num_samples": 1},   # s4 is in no prep
        ]


def test_prep_groups_no_remainder_entry_when_every_sample_has_a_prep(ss):
    def fake(sql, params=None):
        return [("s1",)] if "ORDER BY sample_id" in sql else [("s1", 5, "16S")]
    with patch.object(ss, "pooled_fetchall", side_effect=fake):
        assert [g["prep_id"] for g in ss.prep_groups(232)] == [5]


def test_fetch_prep_samples_limits_and_lists_all_preps(ss):
    meta = [("s1", "a1", "2020-01-01", "soil")]
    calls = []

    def fake(sql, params=None):
        calls.append((sql, params))
        return meta if "anonymized_name" in sql else ROWS
    with patch.object(ss, "pooled_fetchall", side_effect=fake):
        samples, total = ss.fetch_prep_samples(232, 6, 1)
    assert total == 2                                        # s1, s2 are in prep 6
    assert samples == [{"sample_id": "s1", "anonymized_name": "a1", "collection_timestamp": "2020-01-01",
                        "env_package": "soil", "prep_ids": [5, 6]}]
    assert calls[-1][1] == [["s1"]]                          # only the first `limit` ids are fetched


def test_fetch_prep_samples_no_prep(ss):
    def fake(sql, params=None):
        if "anonymized_name" in sql:
            return [("s4", None, None, None)]
        return [("s1",), ("s4",)] if "ORDER BY sample_id" in sql else [("s1", 5, "16S")]
    with patch.object(ss, "pooled_fetchall", side_effect=fake):
        samples, total = ss.fetch_prep_samples(232, None, 500)
    assert (total, [x["sample_id"] for x in samples], samples[0]["prep_ids"]) == (1, ["s4"], [])


# ── artifact_preps ───────────────────────────────────────────────────────────

def test_artifact_preps_maps_artifact_to_prep_and_binds_study(ss):
    with patch.object(ss, "pooled_fetchall", return_value=[(2947, 1115), (2247, 1134)]) as m:
        assert ss.artifact_preps(10317) == {2947: 1115, 2247: 1134}
    sql, params = m.call_args[0]
    assert "preparation_artifact" in sql and "study_artifact" in sql and params == [10317]


def test_artifact_preps_lowest_prep_wins_and_is_memoized(ss):
    # SQL orders by prep id, so a (never seen in practice) artifact in two preps resolves deterministically.
    with patch.object(ss, "pooled_fetchall", return_value=[(7, 100), (7, 200)]) as m:
        assert ss.artifact_preps(5) == {7: 100}
        assert ss.artifact_preps("5") == {7: 100}                 # served from the memo
    assert m.call_count == 1 and "ORDER BY pa.prep_template_id" in m.call_args[0][0]
