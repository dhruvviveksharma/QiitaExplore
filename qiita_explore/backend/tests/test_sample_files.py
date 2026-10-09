"""helpers/sample_files: the per-sample availability map, its filtered view
and facet counts, and its cache-through (memo → study_sample_files_cache →
live compute). Postgres is faked at the helpers.fastq_manifest boundary, which
_study_groups queries through."""

from unittest.mock import patch

import pytest

from .test_fastq_manifest import _fastq_group, _file_row

RAW, TRIM = "Raw upload", "Atropos v1.1.24"
BASE = "/qmounts/qiita_data"


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
        "s1": [("16S", RAW, 10, 2, 0)], "s2": [("16S", RAW, 11, 1, 0)],
    }


def test_summarize_fasta_only(sf):
    files = [("raw_fasta", "FASTA", True, 3220, "SRR1.fna")]
    g = (1928, "16S", "FASTA", RAW, 3220, [("s1", "SRR1")], files, set())
    assert sf.summarize_sample_files([g]) == {"s1": [("16S", RAW, 3220, 0, 1)]}


def test_summarize_keeps_one_entry_per_artifact(sf):
    # Every artifact is its own entry (the artifact id is what the table shows
    # and the Artifact filter picks), sorted by data type, step, artifact id.
    a = _fastq_group(5, "16S", 10, [("s1", "P1")], [], paired=False)
    b = _fastq_group(5, "16S", 11, [("s1", "P1")], [], paired=True)
    fasta = (5, "16S", "FASTA", RAW, 20, [("s1", "P1")], [("raw_fasta", "FASTA", True, 20, "P1.fna")], set())
    meta = _fastq_group(5, "Metagenomic", 30, [("s1", "P1")], [], processing=TRIM)
    assert sf.summarize_sample_files([a, b, fasta, meta]) == {
        "s1": [("16S", RAW, 10, 1, 0), ("16S", RAW, 11, 2, 0), ("16S", RAW, 20, 0, 1),
               ("Metagenomic", TRIM, 30, 2, 0)],
    }


def test_summarize_unmatched_and_null_prefix_absent(sf):
    g = _fastq_group(5, "16S", 10, [("s1", "P1"), ("s2", None)], ["s1", "s2"], paired=False)
    assert sf.summarize_sample_files([g]) == {"s1": [("16S", RAW, 10, 1, 0)]}
    assert sf.summarize_sample_files([]) == {}


def test_encode_decode_round_trip_and_rejects_other_formats(sf):
    m = {"s1": [("16S", RAW, 10, 2, 1), ("Metagenomic", TRIM, 11, 1, 0)], "s2": [("16S", RAW, 12, 0, 1)]}
    assert sf.decode(sf.encode(m)) == m
    assert sf.decode('{"s1": [2, 0]}') is None       # the pre-2026-09-24 shape
    assert sf.decode('{"v":3,"dt":["16S"],"proc":["Raw upload"],"s":{"s1":[[0,0,2,0]]}}') is None
    assert sf.decode("not json") is None


# ── effective / facet_counts ──────────────────────────────────────────────────

MAP = {
    "s1": [("Metagenomic", RAW, 10, 2, 0), ("Metagenomic", TRIM, 11, 2, 0)],
    "s2": [("Full Length Operon", RAW, 12, 0, 1)],
    "s3": [("Metagenomic", RAW, 10, 1, 0)],
}


def test_effective_honours_filter(sf):
    assert sf.effective(MAP, None) == {"s1": (2, 0), "s2": (0, 1), "s3": (1, 0)}
    assert sf.effective(MAP, {"data_types": ["Full Length Operon"], "processing": []}) == {"s2": (0, 1)}
    assert sf.effective(MAP, {"data_types": [], "processing": [TRIM]}) == {"s1": (2, 0)}
    assert sf.effective(MAP, {"data_types": ["16S"], "processing": []}) == {}
    assert sf.effective(MAP, {"data_types": [], "processing": [], "artifacts": ["10"]}) == {"s1": (2, 0), "s3": (1, 0)}


def test_facet_counts_distinct_samples_facet_style(sf):
    dts, procs, arts = sf.facet_counts([MAP], [], None)
    assert dts == [{"name": "Full Length Operon", "count": 1}, {"name": "Metagenomic", "count": 2}]
    assert procs == [{"name": TRIM, "count": 1}, {"name": RAW, "count": 3}]
    # A processing pick never narrows data-type counts (scope ignores it).
    dts, procs, arts = sf.facet_counts([MAP], [], {"data_types": [], "processing": [TRIM]})
    assert dts == [{"name": "Full Length Operon", "count": 1}, {"name": "Metagenomic", "count": 2}]
    assert procs == [{"name": TRIM, "count": 1}, {"name": RAW, "count": 3}]
    # A data-type pick narrows processing counts to files of that type (own selection excluded).
    dts, procs, arts = sf.facet_counts([MAP], [], {"data_types": ["Metagenomic"], "processing": []})
    assert procs == [{"name": TRIM, "count": 1}, {"name": RAW, "count": 2}]
    assert arts == [{"name": "10", "count": 2}, {"name": "11", "count": 1}]   # artifact 12 is not Metagenomic


def test_facet_counts_artifacts_sorted_numerically_and_keep_selected(sf):
    m = {"s1": [("16S", RAW, 9, 1, 0)], "s2": [("16S", RAW, 100, 1, 0)]}
    *_, arts = sf.facet_counts([m], [], {"data_types": [], "processing": [], "artifacts": ["7"]})
    assert [a["name"] for a in arts] == ["7", "9", "100"] and arts[0]["count"] == 0


def test_facet_counts_keeps_selected_names_at_zero(sf):
    dts, _, _ = sf.facet_counts([MAP], [], {"data_types": ["ITS"], "processing": []})
    assert {"name": "ITS", "count": 0} in dts


PREP_MAP = {"s2": ["16S", "Full Length Operon"], "s4": ["16S"]}  # s4 has no file at all


def test_facet_counts_includes_prep_only_types(sf):
    # 16S has no file anywhere; its count comes entirely from prep membership.
    dts, _, _ = sf.facet_counts([MAP], [PREP_MAP], None)
    assert {"name": "16S", "count": 2} in dts
    # s2's Full Length Operon is both a file and a prep type — counted once.
    assert {"name": "Full Length Operon", "count": 1} in dts


def test_facet_counts_prep_only_types_ignore_processing_filter(sf):
    dts, _, _ = sf.facet_counts([MAP], [PREP_MAP], {"data_types": [], "processing": [RAW]})
    assert {"name": "16S", "count": 2} in dts


# ── in_scope ────────────────────────────────────────────────────────────────

def test_in_scope_no_data_type_filter_is_always_true(sf):
    assert sf.in_scope([], [], None)
    assert sf.in_scope(["16S"], [], {"data_types": [], "processing": []})


def test_in_scope_by_prep_membership_or_file(sf):
    f = {"data_types": ["16S"], "processing": []}
    assert sf.in_scope(["16S"], [], f)                       # prep-only, no file
    assert sf.in_scope([], [("16S", RAW, 10, 1, 0)], f)       # file-only, no prep row
    assert not sf.in_scope(["18S"], [("Metagenomic", RAW, 10, 1, 0)], f)


def test_in_scope_artifact_filter_needs_a_file_in_that_artifact(sf):
    f = {"data_types": [], "processing": [], "artifacts": ["10"]}
    assert sf.in_scope(["16S"], [("16S", RAW, 10, 1, 0)], f)
    assert not sf.in_scope(["16S"], [("16S", RAW, 11, 1, 0)], f)
    assert not sf.in_scope(["16S"], [], f)                    # prep-only never survives an artifact pick
    both = {"data_types": ["18S"], "processing": [], "artifacts": ["10"]}
    assert not sf.in_scope(["18S"], [("16S", RAW, 10, 1, 0)], both)


def test_in_scope_ignores_processing_filter(sf):
    f = {"data_types": ["16S"], "processing": ["some other step"]}
    assert sf.in_scope(["16S"], [], f)


# ── get_sample_files caching ──────────────────────────────────────────────────

def _fake_pg(calls):
    def fake(sql, params=None):
        calls["n"] += 1
        return [_file_row()] if "study_artifact" in sql else [("s1", "P1")]
    return fake


EXPECTED = {"s1": [("16S", RAW, 10, 1, 0)]}


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


def test_summarize_pooled_fastq_counts_every_sample_paired(sf):
    files = [("raw_forward_seqs", "raw_data", False, 1, "P_R1.fastq.gz"),
             ("raw_reverse_seqs", "raw_data", False, 1, "Q_R3.fastq.gz")]
    g = (1, "18S", "FASTQ", RAW, 77, [("s1", "P"), ("s2", "P")], files, set())
    assert sf.summarize_sample_files([g]) == {"s1": [("18S", RAW, 77, 2, 0)], "s2": [("18S", RAW, 77, 2, 0)]}


# ── page_files ────────────────────────────────────────────────────────────────

def test_page_files_resolves_only_the_pages_samples_and_artifacts(sf, fm, monkeypatch):
    monkeypatch.setattr(fm, "_BASE", BASE)
    rows = [_file_row(artifact_id=10, filepath="P1_R1.fastq.gz"),
            _file_row(artifact_id=10, filepath="P1_R2.fastq.gz", filepath_type="raw_reverse_seqs"),
            _file_row(artifact_id=10, filepath="P2_R1.fastq.gz")]
    calls = []

    def fake(sql, params=None):
        calls.append((sql, params))
        return rows if "study_artifact" in sql else [("s1", "P1"), ("s2", "P2")]

    all_files = {"s1": [("16S", RAW, 10, 2, 0)], "s2": [("16S", RAW, 10, 1, 0)], "s3": [("16S", RAW, 11, 1, 0)]}
    with patch.object(fm, "pooled_fetchall", side_effect=fake):
        out = sf.page_files(5, ["s1", "s3"], all_files, None)
    assert out["s1"] == [{"artifact_id": 10, "data_type": "16S", "processing": RAW,
                          "r1": f"{BASE}/per_sample_FASTQ/10/P1_R1.fastq.gz",
                          "r2": f"{BASE}/per_sample_FASTQ/10/P1_R2.fastq.gz", "barcodes": ""}]
    assert "s2" not in out and out["s3"] == []         # not on the page / artifact 11 not returned
    assert calls[0][1] == [[5], [10, 11]]              # only the page's artifacts are queried


def test_page_files_honours_filter_and_skips_query_when_nothing_to_resolve(sf, fm):
    all_files = {"s1": [("16S", RAW, 10, 1, 0)]}
    with patch.object(fm, "pooled_fetchall") as m:
        assert sf.page_files(5, ["s1", "s9"], all_files, {"artifacts": ["99"]}) == {"s1": [], "s9": []}
        assert sf.page_files(5, [], all_files, None) == {}
    assert not m.called
