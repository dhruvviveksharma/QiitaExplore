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


def test_fetch_sample_page_sql_and_bind_order(ss):
    ss._columns_cache[232] = (1e18, ["sample_type", "empo_3"])
    calls = []

    def fake(sql, params=None):
        calls.append((sql, params))
        return [(7,)] if sql.lstrip().startswith("SELECT COUNT") else [("s1", "stool", "Animal")]

    with patch.object(ss, "pooled_fetchall", side_effect=fake):
        rows, total, columns = ss.fetch_sample_page(232, 200, 100, "stool")
    assert (rows, total, columns) == ([("s1", "stool", "Animal")], 7, ["sample_type", "empo_3"])

    count_sql, count_params = calls[0]
    assert "sample_id <> %s AND (sample_id ILIKE %s OR sample_values::text ILIKE %s)" in count_sql
    assert count_params == [SENT, "%stool%", "%stool%"]

    page_sql, page_params = calls[1]
    assert "SELECT sample_id, sample_values->>%s, sample_values->>%s FROM qiita.sample_232" in page_sql
    assert page_sql.rstrip().endswith("ORDER BY sample_id LIMIT %s OFFSET %s")
    # select-list column names -> WHERE params -> LIMIT -> OFFSET
    assert page_params == ["sample_type", "empo_3", SENT, "%stool%", "%stool%", 100, 200]


def test_fetch_sample_page_without_filter_or_columns(ss):
    ss._columns_cache[5] = (1e18, [])
    with patch.object(ss, "pooled_fetchall", side_effect=[[(0,)], []]) as m:
        assert ss.fetch_sample_page(5, 0, 50) == ([], 0, [])
    sql, params = m.call_args_list[1][0]
    assert "SELECT sample_id FROM qiita.sample_5 WHERE sample_id <> %s ORDER BY" in sql
    assert "ILIKE" not in sql
    assert params == [SENT, 50, 0]


def test_matching_sample_ids(ss):
    with patch.object(ss, "pooled_fetchall", return_value=[("s2",)]) as m:
        assert ss.matching_sample_ids(232, " skin ") == ["s2"]
    sql, params = m.call_args[0]
    assert "ILIKE" in sql
    assert params == [SENT, "%skin%", "%skin%"]
