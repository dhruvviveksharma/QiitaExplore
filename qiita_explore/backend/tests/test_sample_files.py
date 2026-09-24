"""helpers/sample_files: the per-sample availability map, its filtered view
and facet counts, and its cache-through (memo → study_sample_files_cache →
live compute). Postgres is faked at the helpers.fastq_manifest boundary, which
_study_groups queries through."""

from unittest.mock import patch

import pytest

from .test_fastq_manifest import _fastq_group, _file_row

RAW, TRIM = "Raw upload", "Atropos v1.1.24"


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

def test_summarize_paired_and_single_fastq(sf):
    paired = _fastq_group(5, "16S", 10, [("s1", "P1")], ["s1"], paired=True)
    single = _fastq_group(5, "16S", 11, [("s2", "P2")], ["s2"], paired=False)
    assert sf.summarize_sample_files([paired, single]) == {
        "s1": [("16S", RAW, 2, 0)], "s2": [("16S", RAW, 1, 0)],
    }


def test_summarize_fasta_only(sf):
    files = [("raw_fasta", "FASTA", True, 3220, "SRR1.fna")]
    g = (1928, "16S", "FASTA", RAW, [("s1", "SRR1")], files, set())
    assert sf.summarize_sample_files([g]) == {"s1": [("16S", RAW, 0, 1)]}


def test_summarize_merges_same_pair_and_keeps_others_apart(sf):
    # Two raw 16S artifacts collapse into one entry (max); FASTA ORs in; a
    # second data type and a second processing step are entries of their own.
    a = _fastq_group(5, "16S", 10, [("s1", "P1")], [], paired=False)
    b = _fastq_group(5, "16S", 11, [("s1", "P1")], [], paired=True)
    fasta = (5, "16S", "FASTA", RAW, [("s1", "P1")], [("raw_fasta", "FASTA", True, 20, "P1.fna")], set())
    meta = _fastq_group(5, "Metagenomic", 30, [("s1", "P1")], [], processing=TRIM)
    assert sf.summarize_sample_files([a, b, fasta, meta]) == {
        "s1": [("16S", RAW, 2, 1), ("Metagenomic", TRIM, 2, 0)],
    }


def test_summarize_unmatched_and_null_prefix_absent(sf):
    g = _fastq_group(5, "16S", 10, [("s1", "P1"), ("s2", None)], ["s1", "s2"], paired=False)
    assert sf.summarize_sample_files([g]) == {"s1": [("16S", RAW, 1, 0)]}
    assert sf.summarize_sample_files([]) == {}


def test_encode_decode_round_trip_and_rejects_other_formats(sf):
    m = {"s1": [("16S", RAW, 2, 1), ("Metagenomic", TRIM, 1, 0)], "s2": [("16S", RAW, 0, 1)]}
    assert sf.decode(sf.encode(m)) == m
    assert sf.decode('{"s1": [2, 0]}') is None       # the pre-2026-09-24 shape
    assert sf.decode("not json") is None


# ── effective / facet_counts ──────────────────────────────────────────────────

MAP = {
    "s1": [("Metagenomic", RAW, 2, 0), ("Metagenomic", TRIM, 2, 0)],
    "s2": [("Full Length Operon", RAW, 0, 1)],
    "s3": [("Metagenomic", RAW, 1, 0)],
}


def test_effective_honours_filter(sf):
    assert sf.effective(MAP, None) == {"s1": (2, 0), "s2": (0, 1), "s3": (1, 0)}
    assert sf.effective(MAP, {"data_types": ["Full Length Operon"], "processing": []}) == {"s2": (0, 1)}
    assert sf.effective(MAP, {"data_types": [], "processing": [TRIM]}) == {"s1": (2, 0)}
    assert sf.effective(MAP, {"data_types": ["16S"], "processing": []}) == {}


def test_facet_counts_distinct_samples_facet_style(sf):
    dts, procs = sf.facet_counts([MAP], None)
    assert dts == [{"name": "Full Length Operon", "count": 1}, {"name": "Metagenomic", "count": 2}]
    assert procs == [{"name": TRIM, "count": 1}, {"name": RAW, "count": 3}]
    # Picking a processing step narrows the data-type counts but not its own list.
    dts, procs = sf.facet_counts([MAP], {"data_types": [], "processing": [TRIM]})
    assert dts == [{"name": "Metagenomic", "count": 1}]
    assert procs == [{"name": TRIM, "count": 1}, {"name": RAW, "count": 3}]


def test_facet_counts_keeps_selected_names_at_zero(sf):
    dts, _ = sf.facet_counts([MAP], {"data_types": ["ITS"], "processing": []})
    assert {"name": "ITS", "count": 0} in dts


# ── get_sample_files caching ──────────────────────────────────────────────────

def _fake_pg(calls):
    def fake(sql, params=None):
        calls["n"] += 1
        return [_file_row()] if "study_artifact" in sql else [("s1", "P1")]
    return fake


EXPECTED = {"s1": [("16S", RAW, 1, 0)]}


def test_computes_then_persists_to_own_table(sf, fm):
    calls = {"n": 0}
    with patch.object(fm, "pooled_fetchall", side_effect=_fake_pg(calls)):
        assert sf.get_sample_files(232) == EXPECTED
    assert calls["n"] == 2  # one files query, one prep-samples query

    from store.cache import get_study_detail_cache, get_study_sample_files_cache
    assert sf.decode(get_study_sample_files_cache(232)) == EXPECTED
    # TKT-086: the map no longer creates a study_detail_cache row, so the
    # study modal can never mistake it for a (preps-less) cache hit.
    assert get_study_detail_cache(232) is None


def test_served_from_process_memo(sf, fm):
    with patch.object(fm, "pooled_fetchall", side_effect=_fake_pg({"n": 0})):
        sf.get_sample_files(232)
    with patch.object(fm, "pooled_fetchall") as m:
        assert sf.get_sample_files(232) == EXPECTED
    assert not m.called


def test_served_from_table_after_memo_cleared(sf, fm):
    with patch.object(fm, "pooled_fetchall", side_effect=_fake_pg({"n": 0})):
        sf.get_sample_files(232)
    sf._memo.clear()
    with patch.object(fm, "pooled_fetchall") as m:
        assert sf.get_sample_files(232) == EXPECTED
    assert not m.called


def test_expired_or_old_format_row_is_recomputed(sf, fm, db_conn):
    with patch.object(fm, "pooled_fetchall", side_effect=_fake_pg({"n": 0})):
        sf.get_sample_files(232)
    for sql in ("UPDATE study_sample_files_cache SET cached_at='2000-01-01T00:00:00Z'",
                "UPDATE study_sample_files_cache SET cached_at=NULL, sample_files_json='{\"s1\": [1, 0]}'"):
        db_conn.execute(sql)
        db_conn.commit()
        sf._memo.clear()
        calls = {"n": 0}
        with patch.object(fm, "pooled_fetchall", side_effect=_fake_pg(calls)):
            assert sf.get_sample_files(232) == EXPECTED
        assert calls["n"] == 2
