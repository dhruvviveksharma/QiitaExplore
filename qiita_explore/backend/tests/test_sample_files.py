"""helpers/sample_files: the per-sample availability map and its cache-through
(memo → study_sample_files_cache → live compute). Postgres is faked at the
helpers.fastq_manifest boundary, which _study_groups queries through."""

import json
from unittest.mock import patch

import pytest

from .test_fastq_manifest import _fastq_group, _file_row


@pytest.fixture
def sf():
    import helpers.sample_files as mod
    mod._memo.clear()
    return mod


@pytest.fixture
def fm():
    import helpers.fastq_manifest as mod
    return mod


# ── summarize_sample_files ────────────────────────────────────────────────────

def test_summarize_paired_fastq(sf):
    g = _fastq_group(5, "16S", 10, [("s1", "P1")], ["s1"], paired=True)
    assert sf.summarize_sample_files([g]) == {"s1": [2, 0]}


def test_summarize_single_fastq(sf):
    g = _fastq_group(5, "16S", 10, [("s1", "P1")], ["s1"], paired=False)
    assert sf.summarize_sample_files([g]) == {"s1": [1, 0]}


def test_summarize_fasta_only(sf):
    files = [("raw_fasta", "FASTA", True, 3220, "SRR1.fna")]
    g = (1928, "16S", "FASTA", [("s1", "SRR1")], files, set())
    assert sf.summarize_sample_files([g]) == {"s1": [0, 1]}


def test_summarize_sample_in_both_fastq_and_fasta(sf):
    fastq_g = _fastq_group(5, "16S", 10, [("s1", "P1")], ["s1"], paired=True)
    fasta_files = [("raw_fasta", "FASTA", True, 20, "P1.fna")]
    fasta_g = (5, "16S", "FASTA", [("s1", "P1")], fasta_files, set())
    assert sf.summarize_sample_files([fastq_g, fasta_g]) == {"s1": [2, 1]}


def test_summarize_unmatched_and_null_prefix_absent(sf):
    g = _fastq_group(5, "16S", 10, [("s1", "P1"), ("s2", None)], ["s1", "s2"], paired=False)
    assert sf.summarize_sample_files([g]) == {"s1": [1, 0]}
    assert sf.summarize_sample_files([]) == {}


# ── get_sample_files caching ──────────────────────────────────────────────────

def _fake_pg(calls):
    def fake(sql, params=None):
        calls["n"] += 1
        return [_file_row()] if "study_artifact" in sql else [("s1", "P1")]
    return fake


def test_computes_then_persists_to_own_table(sf, fm):
    calls = {"n": 0}
    with patch.object(fm, "pooled_fetchall", side_effect=_fake_pg(calls)):
        assert sf.get_sample_files(232) == {"s1": [1, 0]}
    assert calls["n"] == 2  # one files query, one prep-samples query

    from store.cache import get_study_detail_cache, get_study_sample_files_cache
    assert json.loads(get_study_sample_files_cache(232)) == {"s1": [1, 0]}
    # TKT-086: the map no longer creates a study_detail_cache row, so the
    # study modal can never mistake it for a (preps-less) cache hit.
    assert get_study_detail_cache(232) is None


def test_served_from_process_memo(sf, fm):
    with patch.object(fm, "pooled_fetchall", side_effect=_fake_pg({"n": 0})):
        sf.get_sample_files(232)
    with patch.object(fm, "pooled_fetchall") as m:
        assert sf.get_sample_files(232) == {"s1": [1, 0]}
    assert not m.called


def test_served_from_table_after_memo_cleared(sf, fm):
    with patch.object(fm, "pooled_fetchall", side_effect=_fake_pg({"n": 0})):
        sf.get_sample_files(232)
    sf._memo.clear()
    with patch.object(fm, "pooled_fetchall") as m:
        assert sf.get_sample_files(232) == {"s1": [1, 0]}
    assert not m.called


def test_expired_row_is_recomputed(sf, fm, db_conn):
    with patch.object(fm, "pooled_fetchall", side_effect=_fake_pg({"n": 0})):
        sf.get_sample_files(232)
    db_conn.execute("UPDATE study_sample_files_cache SET cached_at='2000-01-01T00:00:00Z'")
    db_conn.commit()
    sf._memo.clear()
    calls = {"n": 0}
    with patch.object(fm, "pooled_fetchall", side_effect=_fake_pg(calls)):
        sf.get_sample_files(232)
    assert calls["n"] == 2
