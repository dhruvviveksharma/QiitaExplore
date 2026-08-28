"""Unit tests for merge pipeline fixes.

Covers three behaviors introduced/fixed in the merge workflow:
1. Artifact deduplication (qiita_fetch._fetch_study_detail_from_qiita)
   - The DB returns one row per file per artifact; we must keep the .biom path.
2. sample_ids filtering in metadata write (merge_executor._write_merged_sample_metadata)
   - Only samples in the workspace filter are written to the TSV.
3. TSV trimming to actual BIOM samples (remote_merge.main)
   - After merge, rows whose #SampleID is not in the merged BIOM are dropped.
"""

import csv
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers that replicate production logic without requiring the Qiita DB stack
# ---------------------------------------------------------------------------

def _run_dedup(raw_rows):
    """Reproduce the artifact-by-id dedup from qiita_fetch._fetch_study_detail_from_qiita.

    raw_rows: list of (prep_id, prep_name, artifact_id, artifact_type, data_type, full_path, ts)
    """
    artifact_by_id = {}
    for r in raw_rows:
        aid = r[2]
        path = r[5]
        existing = artifact_by_id.get(aid)
        if existing is None or path.lower().endswith(".biom"):
            artifact_by_id[aid] = {
                "artifact_id": aid,
                "artifact_type": r[3],
                "data_type": r[4],
                "full_path": path,
                "generated_timestamp": str(r[6]) if r[6] else None,
            }
    return list(artifact_by_id.values())


def _filter_tsv_to_ids(tsv_path: Path, keep_ids: set):
    """Reproduce TSV filtering from remote_merge.main."""
    with open(tsv_path, newline="") as fh:
        all_rows = list(csv.reader(fh, delimiter="\t"))
    header = all_rows[:1]
    filtered = [r for r in all_rows[1:] if r and r[0] in keep_ids]
    with open(tsv_path, "w", newline="") as fh:
        csv.writer(fh, delimiter="\t", lineterminator="\n").writerows(header + filtered)


def _read_tsv(path):
    with open(path, newline="") as f:
        return list(csv.reader(f, delimiter="\t"))


# ---------------------------------------------------------------------------
# 1. Artifact deduplication
# ---------------------------------------------------------------------------

class TestArtifactDedup:
    """qiita_fetch: one artifact_id → one dict entry, preferring the .biom path."""

    def test_biom_preferred_over_html(self):
        rows = [
            (1, "Prep", 999, "BIOM", "16S", "/data/BIOM/999/index.html", "2020-01-01"),
            (1, "Prep", 999, "BIOM", "16S", "/data/BIOM/999/all.biom", "2020-01-01"),
        ]
        result = _run_dedup(rows)
        assert len(result) == 1
        assert result[0]["full_path"].endswith("all.biom")

    def test_regression_study101_artifact44403(self):
        """Reproduces the original bug: index.html was first in the DB result for artifact 44403."""
        rows = [
            (1, "P", 44403, "BIOM", "16S", "/qmounts/qiita_data/BIOM/44403/index.html", "2018-01-22 10:46:59"),
            (1, "P", 44403, "BIOM", "16S", "/qmounts/qiita_data/BIOM/44403/all.biom", "2018-01-22 10:46:59"),
            (1, "P", 44403, "BIOM", "16S", "/qmounts/qiita_data/BIOM/44403/all.seqs.fa", "2018-01-22 10:46:59"),
            (1, "P", 44403, "BIOM", "16S", "/qmounts/qiita_data/BIOM/44403/support_files", "2018-01-22 10:46:59"),
        ]
        result = _run_dedup(rows)
        assert len(result) == 1
        assert result[0]["full_path"] == "/qmounts/qiita_data/BIOM/44403/all.biom"

    def test_multiple_artifacts_each_resolved_to_biom(self):
        rows = [
            (1, "P", 44402, "BIOM", "16S", "/q/BIOM/44402/index.html", "2018-01-22 10:46:56"),
            (1, "P", 44402, "BIOM", "16S", "/q/BIOM/44402/reference-hit.biom", "2018-01-22 10:46:56"),
            (1, "P", 44403, "BIOM", "16S", "/q/BIOM/44403/index.html", "2018-01-22 10:46:59"),
            (1, "P", 44403, "BIOM", "16S", "/q/BIOM/44403/all.biom", "2018-01-22 10:46:59"),
        ]
        result = _run_dedup(rows)
        by_id = {a["artifact_id"]: a for a in result}
        assert len(by_id) == 2
        assert by_id[44402]["full_path"].endswith("reference-hit.biom")
        assert by_id[44403]["full_path"].endswith("all.biom")

    def test_single_biom_file_unchanged(self):
        rows = [(1, "P", 100, "BIOM", "16S", "/q/BIOM/100/all.biom", "2020-01-01")]
        result = _run_dedup(rows)
        assert len(result) == 1
        assert result[0]["full_path"] == "/q/BIOM/100/all.biom"

    def test_no_biom_extension_keeps_first_row(self):
        """If an artifact has no .biom file at all, first row survives (no crash)."""
        rows = [
            (1, "P", 5, "BIOM", "16S", "/q/BIOM/5/index.html", "2020-01-01"),
            (1, "P", 5, "BIOM", "16S", "/q/BIOM/5/seqs.fa", "2020-01-01"),
        ]
        result = _run_dedup(rows)
        assert len(result) == 1
        assert result[0]["full_path"] == "/q/BIOM/5/index.html"


# ---------------------------------------------------------------------------
# 2. Sample-ID filtering in _write_merged_sample_metadata
# ---------------------------------------------------------------------------

class TestWriteMergedSampleMetadata:
    """merge_executor._write_merged_sample_metadata: correct samples end up in TSV."""

    def _call(self, tmp_path, snap, fetch_return):
        """Import and call _write_merged_sample_metadata with a mocked DB fetch."""
        # Clear any cached import so the patch takes effect
        sys.modules.pop("helpers.merge_executor", None)

        mock_qiita = MagicMock(_fetch_full_sample_metadata=MagicMock(return_value=fetch_return))
        with patch.dict(sys.modules, {"helpers.qiita_fetch": mock_qiita}):
            # Re-import so lazy import inside the function picks up the mock
            sys.modules.pop("helpers.merge_executor", None)
            from helpers.merge_executor import _write_merged_sample_metadata as fn
            return fn(tmp_path, snap)

    def test_all_samples_written_when_no_filter(self, tmp_path):
        fake_rows = [
            {"sample_id": "101.s1", "fields": {"age": "10", "sex": "male"}},
            {"sample_id": "101.s2", "fields": {"age": "20", "sex": "female"}},
        ]
        result = self._call(tmp_path, [{"study_id": 101, "sample_ids": None}], fake_rows)

        assert result is True
        ids = [r[0] for r in _read_tsv(tmp_path / "sample_metadata.tsv")[1:]]
        assert ids == ["101.s1", "101.s2"]

    def test_filters_to_sample_ids_when_set(self, tmp_path):
        fake_rows = [
            {"sample_id": "101.s1", "fields": {"age": "10"}},
            {"sample_id": "101.s2", "fields": {"age": "20"}},
            {"sample_id": "101.s3", "fields": {"age": "30"}},
        ]
        snap = [{"study_id": 101, "sample_ids": ["101.s1", "101.s3"]}]
        result = self._call(tmp_path, snap, fake_rows)

        assert result is True
        ids = [r[0] for r in _read_tsv(tmp_path / "sample_metadata.tsv")[1:]]
        assert "101.s1" in ids
        assert "101.s3" in ids
        assert "101.s2" not in ids

    def test_union_columns_across_studies(self, tmp_path):
        """Columns from both studies are unioned; missing values filled with 'not applicable'."""
        sys.modules.pop("helpers.merge_executor", None)

        rows_77 = [{"sample_id": "77.A", "fields": {"age": "5", "sex": "male"}}]
        rows_101 = [{"sample_id": "101.B", "fields": {"age": "30", "country": "USA"}}]

        def fake_fetch(study_id, limit):
            return rows_77 if study_id == 77 else rows_101

        mock_qiita = MagicMock(_fetch_full_sample_metadata=fake_fetch)
        with patch.dict(sys.modules, {"helpers.qiita_fetch": mock_qiita}):
            sys.modules.pop("helpers.merge_executor", None)
            from helpers.merge_executor import _write_merged_sample_metadata as fn
            result = fn(tmp_path, [
                {"study_id": 77, "sample_ids": None},
                {"study_id": 101, "sample_ids": None},
            ])

        assert result is True
        rows = _read_tsv(tmp_path / "sample_metadata.tsv")
        header = rows[0]
        assert "age" in header
        assert "country" in header
        assert "sex" in header
        sample_ids = [r[0] for r in rows[1:]]
        assert "77.A" in sample_ids
        assert "101.B" in sample_ids

    def test_returns_false_when_fetch_raises(self, tmp_path):
        fake_rows_raise = MagicMock(side_effect=RuntimeError("DB down"))
        snap = [{"study_id": 101, "sample_ids": None}]
        mock_qiita = MagicMock(_fetch_full_sample_metadata=fake_rows_raise)

        sys.modules.pop("helpers.merge_executor", None)

        with patch.dict(sys.modules, {"helpers.qiita_fetch": mock_qiita}):
            sys.modules.pop("helpers.merge_executor", None)
            from helpers.merge_executor import _write_merged_sample_metadata as fn
            result = fn(tmp_path, snap)

        assert result is False
        assert not (tmp_path / "sample_metadata.tsv").exists()


# ---------------------------------------------------------------------------
# 3. TSV trimming to BIOM sample IDs (remote_merge.main)
# ---------------------------------------------------------------------------

class TestRemoteMergeTsvTrimming:
    """After BIOM merge, sample_metadata.tsv is trimmed to samples actually in merged.biom."""

    def _write_tsv(self, path, rows):
        with open(path, "w", newline="") as f:
            csv.writer(f, delimiter="\t", lineterminator="\n").writerows(rows)

    def test_rows_not_in_biom_are_removed(self, tmp_path):
        tsv = tmp_path / "sample_metadata.tsv"
        self._write_tsv(tsv, [
            ["#SampleID", "age"],
            ["77.A", "10"],
            ["77.B", "20"],
            ["101.X", "30"],
        ])
        _filter_tsv_to_ids(tsv, {"77.A", "77.B"})

        rows = _read_tsv(tsv)
        ids = [r[0] for r in rows[1:]]
        assert ids == ["77.A", "77.B"]
        assert "101.X" not in ids

    def test_header_is_preserved(self, tmp_path):
        tsv = tmp_path / "sample_metadata.tsv"
        self._write_tsv(tsv, [
            ["#SampleID", "age", "country"],
            ["77.A", "10", "USA"],
        ])
        _filter_tsv_to_ids(tsv, {"77.A"})

        rows = _read_tsv(tsv)
        assert rows[0] == ["#SampleID", "age", "country"]

    def test_all_rows_kept_when_all_in_biom(self, tmp_path):
        tsv = tmp_path / "sample_metadata.tsv"
        self._write_tsv(tsv, [
            ["#SampleID", "age"],
            ["77.A", "10"],
            ["101.B", "20"],
        ])
        _filter_tsv_to_ids(tsv, {"77.A", "101.B"})

        ids = [r[0] for r in _read_tsv(tsv)[1:]]
        assert ids == ["77.A", "101.B"]

    def test_all_rows_removed_when_none_in_biom(self, tmp_path):
        tsv = tmp_path / "sample_metadata.tsv"
        self._write_tsv(tsv, [
            ["#SampleID", "age"],
            ["77.A", "10"],
        ])
        _filter_tsv_to_ids(tsv, set())

        rows = _read_tsv(tsv)
        assert len(rows) == 1  # header only
