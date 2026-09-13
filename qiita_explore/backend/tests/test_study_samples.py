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
