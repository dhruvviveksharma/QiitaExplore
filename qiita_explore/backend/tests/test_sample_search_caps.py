"""_MAX_KEYWORDS_PER_PROBE must bound EVERY JSONB probe, not just
_probe_fields_raw — otherwise a domain-synonym-expanded keyword list (up to
80 terms) turns each per-study probe into dozens of full-text ILIKE scans
under a fixed statement timeout.
"""
from unittest.mock import MagicMock

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()

from helpers.sample_search import (  # noqa: E402
    _MAX_KEYWORDS_PER_PROBE, _probe_study_raw, _score_sample_metadata_raw,
)


def _fake_pool():
    """A pool whose getconn() returns a fake connection capturing the
    (sql, params) it was executed with."""
    captured = []
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.execute = MagicMock(side_effect=lambda sql, params: captured.append(params))
    cursor.fetchone = MagicMock(return_value=(True,))
    conn.cursor = MagicMock(return_value=cursor)
    pool = MagicMock()
    pool.getconn = MagicMock(return_value=conn)
    pool.putconn = MagicMock()
    return pool, captured


def _thirty_keywords():
    return [f"kw{i:02d}" for i in range(30)]


class TestProbeStudyRawCapsKeywords:
    def test_binds_only_the_first_ten(self):
        pool, captured = _fake_pool()
        kws = _thirty_keywords()
        _probe_study_raw(pool, 1, kws)

        assert len(captured) == 1
        bound_kws = captured[0][0]
        assert len(bound_kws) == _MAX_KEYWORDS_PER_PROBE
        assert bound_kws == kws[:_MAX_KEYWORDS_PER_PROBE]


class TestScoreSampleMetadataRawCapsKeywords:
    def test_binds_only_the_first_ten(self):
        pool, captured = _fake_pool()
        kws = _thirty_keywords()
        _score_sample_metadata_raw(pool, 1, kws)

        assert len(captured) == 1
        bound_kws = captured[0][0]
        assert len(bound_kws) == _MAX_KEYWORDS_PER_PROBE
        assert bound_kws == kws[:_MAX_KEYWORDS_PER_PROBE]
