"""helpers/qiita_fetch sample lists: read qiita.sample_<id> by its primary key,
without the qiita.study_sample join that made AGP's first study open take 12 s.
Postgres is faked at _qiita_fetch."""

import pytest


@pytest.fixture
def qf(fresh_db):
    import helpers.qiita_fetch as mod
    return mod


def _capture(qf, monkeypatch, results):
    calls = []

    def fake(sql, params=(), default=None):
        calls.append((" ".join(sql.split()), list(params)))
        return results.pop(0)
    monkeypatch.setattr(qf, "_qiita_fetch", fake)
    return calls


def test_study_samples_reads_the_sample_table_by_primary_key(qf, monkeypatch):
    calls = _capture(qf, monkeypatch, [[(41600,)], [("10317.0001", "a1", "2016-01-01", "human-gut")]])
    samples, total = qf._fetch_study_samples(10317, limit=200)
    assert total == 41600
    assert samples == [{"sample_id": "10317.0001", "anonymized_name": "a1",
                        "collection_timestamp": "2016-01-01", "env_package": "human-gut"}]
    count_sql, list_sql = calls[0][0], calls[1][0]
    assert "FROM qiita.study_sample WHERE study_id = %s" in count_sql        # the cheap count stays
    assert "FROM qiita.sample_10317 WHERE sample_id <> %s ORDER BY sample_id LIMIT %s" in list_sql
    assert "study_sample" not in list_sql and "JOIN" not in list_sql
    assert calls[1][1] == ["qiita_sample_column_names", 200]


def test_full_sample_metadata_reads_the_sample_table_by_primary_key(qf, monkeypatch):
    calls = _capture(qf, monkeypatch, [[("101.1", {"env": "gut"})]])
    assert qf._fetch_full_sample_metadata(101, limit=5) == [{"sample_id": "101.1", "fields": {"env": "gut"}}]
    sql, params = calls[0]
    assert "FROM qiita.sample_101 WHERE sample_id <> %s ORDER BY sample_id LIMIT %s" in sql
    assert "study_sample" not in sql and "JOIN" not in sql
    assert params == ["qiita_sample_column_names", 5]
