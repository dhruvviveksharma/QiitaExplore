"""Tests for the per-sample FASTQ manifest (helpers/fastq_manifest.py + its route).

The matcher/serializer are pure Python and take base_dir explicitly, so they
run without Postgres. The route test reuses the test_stream_routes app
pattern and fakes the two helper boundaries.
"""
import csv
import os
import sys
from unittest.mock import patch

import pytest

from .conftest import stub_qiita_db_and_core

BASE = "/qmounts/qiita_data"


@pytest.fixture
def fm():
    import helpers.fastq_manifest as mod
    return mod


# ── build_manifest_rows ──────────────────────────────────────────────────────

def test_paired_modern_subdirectory(fm):
    prefix = "1E7F1CF_A_FW2_F17_S33_L001"
    files = [
        ("raw_forward_seqs", "per_sample_FASTQ", True, 233553, f"{prefix}_R1_001.trimmed.fastq.gz"),
        ("raw_reverse_seqs", "per_sample_FASTQ", True, 233553, f"{prefix}_R2_001.trimmed.fastq.gz"),
    ]
    rows, paired = fm.build_manifest_rows([("s1", prefix)], files, BASE)
    assert paired is True
    assert rows == [(
        "s1",
        f"{BASE}/per_sample_FASTQ/233553/{prefix}_R1_001.trimmed.fastq.gz",
        f"{BASE}/per_sample_FASTQ/233553/{prefix}_R2_001.trimmed.fastq.gz",
    )]


def test_legacy_single_end_substring_match(fm):
    # subdirectory=False → no artifact_id segment; file renamed "{obj_id}_{basename}"
    files = [("raw_forward_seqs", "raw_data", False, 2214, "360_SRR1561443.fastq.gz")]
    rows, paired = fm.build_manifest_rows([("10219.SRR1561443", "SRR1561443")], files, BASE)
    assert paired is False
    assert rows == [("10219.SRR1561443", f"{BASE}/raw_data/360_SRR1561443.fastq.gz", None)]


@pytest.mark.parametrize("order", [("100", "1002"), ("1002", "100")])
def test_prefix_collision_longest_first(fm, order):
    samples = [(f"s{p}", p) for p in order]
    files = [
        ("raw_forward_seqs", "per_sample_FASTQ", True, 7, "100_R1.fastq.gz"),
        ("raw_forward_seqs", "per_sample_FASTQ", True, 7, "1002_R1.fastq.gz"),
    ]
    rows, _ = fm.build_manifest_rows(samples, files, BASE)
    assert dict((sid, os.path.basename(f)) for sid, f, _ in rows) == {
        "s100": "100_R1.fastq.gz", "s1002": "1002_R1.fastq.gz",
    }


def test_unmatched_and_null_prefix_skipped(fm):
    files = [("raw_forward_seqs", "per_sample_FASTQ", True, 7, "ABC_R1.fastq.gz")]
    rows, _ = fm.build_manifest_rows([("s_zzz", "ZZZ"), ("s_null", None), ("s_ok", "ABC")], files, BASE)
    assert [r[0] for r in rows] == ["s_ok"]


def test_rows_sorted_by_sample_id(fm):
    files = [
        ("raw_forward_seqs", "per_sample_FASTQ", True, 7, "bbb_R1.fastq.gz"),
        ("raw_forward_seqs", "per_sample_FASTQ", True, 7, "aaa_R1.fastq.gz"),
    ]
    rows, _ = fm.build_manifest_rows([("z", "bbb"), ("a", "aaa")], files, BASE)
    assert [r[0] for r in rows] == ["a", "z"]


def test_raw_fasta_is_the_forward_file(fm):
    files = [("raw_fasta", "FASTA", True, 3220, "SRR040501.fna")]
    rows, paired = fm.build_manifest_rows([("1928.SRR040501", "SRR040501")], files, BASE)
    assert paired is False
    assert rows == [("1928.SRR040501", f"{BASE}/FASTA/3220/SRR040501.fna", None)]


# ── build_csv_rows / to_csv ──────────────────────────────────────────────────

def _fastq_group(study_id, data_type, artifact_id, samples, allow, paired=True):
    files = []
    for _, prefix in samples:
        files.append(("raw_forward_seqs", "per_sample_FASTQ", True, artifact_id, f"{prefix}_R1.fastq.gz"))
        if paired:
            files.append(("raw_reverse_seqs", "per_sample_FASTQ", True, artifact_id, f"{prefix}_R2.fastq.gz"))
    return (study_id, data_type, "per_sample_FASTQ", samples, files, set(allow))


def test_csv_paired_sample_is_two_rows_with_file_types(fm):
    rows = fm.build_csv_rows([_fastq_group(16326, "16S", 10, [("s1", "P1")], ["s1"])], BASE)
    assert rows == [
        (16326, "s1", f"{BASE}/per_sample_FASTQ/10/P1_R1.fastq.gz", "16S", "raw_forward_seqs"),
        (16326, "s1", f"{BASE}/per_sample_FASTQ/10/P1_R2.fastq.gz", "16S", "raw_reverse_seqs"),
    ]


def test_csv_fasta_group(fm):
    files = [("raw_fasta", "FASTA", True, 3220, "SRR1.fna")]
    rows = fm.build_csv_rows([(1928, "16S", "FASTA", [("s1", "SRR1")], files, {"s1"})], BASE)
    assert rows == [(1928, "s1", f"{BASE}/FASTA/3220/SRR1.fna", "16S", "raw_fasta")]


def test_csv_allowlist_filters_after_matching(fm):
    # 's8B4' is selected, 's8B4ABX' is not. If unselected samples were dropped
    # before matching, '8B4' would claim '8B4ABX_R1.fastq.gz' (longest first
    # needs the whole prep present).
    samples = [("s8B4", "8B4"), ("s8B4ABX", "8B4ABX")]
    rows = fm.build_csv_rows([_fastq_group(1, "16S", 7, samples, ["s8B4"], paired=False)], BASE)
    assert [r[1] for r in rows] == ["s8B4"]
    assert rows[0][2].endswith("/8B4_R1.fastq.gz")


def test_csv_sample_in_two_preps_yields_both_data_types(fm):
    g1 = _fastq_group(5, "16S", 10, [("s1", "P1")], ["s1"], paired=False)
    g2 = _fastq_group(5, "Metagenomic", 20, [("s1", "P1")], ["s1"], paired=False)
    rows = fm.build_csv_rows([g1, g2], BASE)
    assert [(r[3], r[4]) for r in rows] == [("16S", "raw_forward_seqs"), ("Metagenomic", "raw_forward_seqs")]
    assert len({r[2] for r in rows}) == 2


def test_csv_dedupes_identical_rows_and_sorts(fm):
    g = _fastq_group(5, "16S", 10, [("s2", "P2"), ("s1", "P1")], ["s1", "s2"], paired=False)
    rows = fm.build_csv_rows([g, g], BASE)
    assert [r[1] for r in rows] == ["s1", "s2"]


def test_csv_unselected_and_unmatched_omitted(fm):
    g = _fastq_group(5, "16S", 10, [("s1", "P1"), ("s2", None)], ["s1", "s2", "ghost"], paired=False)
    assert [r[1] for r in fm.build_csv_rows([g], BASE)] == ["s1"]
    assert fm.build_csv_rows([], BASE) == []


def test_to_csv(fm):
    out = fm.to_csv([(5, "s1", "/p/a_R1.fq.gz", "16S", "raw_forward_seqs")])
    assert out == ("study_id,sample_id,file_path_in_qmounts,data_type,file_type\n"
                   "5,s1,/p/a_R1.fq.gz,16S,raw_forward_seqs\n")


def test_to_csv_spreadsheet_safe_wraps_only_sample_id(fm):
    # A sample id like "10317.000001062" parses as a float in Excel/Numbers
    # and displays rounded — indistinguishable from the study_id column.
    # ="..." forces it to stay text; study_id and the other columns are
    # untouched.
    out = fm.to_csv([(5, "10317.000001", "/p/a_R1.fq.gz", "16S", "raw_forward_seqs")], spreadsheet_safe=True)
    rows = list(csv.reader(out.splitlines()))
    assert rows[0] == ["study_id", "sample_id", "file_path_in_qmounts", "data_type", "file_type"]
    assert rows[1] == ["5", '="10317.000001"', "/p/a_R1.fq.gz", "16S", "raw_forward_seqs"]
    plain = fm.to_csv([(5, "10317.000001", "/p/a_R1.fq.gz", "16S", "raw_forward_seqs")])
    assert list(csv.reader(plain.splitlines()))[1] == \
        ["5", "10317.000001", "/p/a_R1.fq.gz", "16S", "raw_forward_seqs"]


# ── _study_groups ──────────────────────────────────────────────────────────────

def _file_row(study_id=5, artifact_id=10, prep_id=100, filepath_type="raw_forward_seqs",
              mountpoint="per_sample_FASTQ", subdirectory=True, filepath="P1_R1.fastq.gz",
              data_type="16S", artifact_type="per_sample_FASTQ"):
    return (study_id, artifact_id, prep_id, filepath_type, mountpoint, subdirectory, filepath,
            data_type, artifact_type)


def test_study_groups_shape(fm):
    calls = []

    def fake(sql, params=None):
        calls.append((sql, params))
        return [_file_row()] if "study_artifact" in sql else [("s1", "P1")]

    with patch.object(fm, "pooled_fetchall", side_effect=fake):
        groups = fm._study_groups([5], {5: {"s1"}})
    assert len(groups) == 1
    study_id, data_type, artifact_type, samples, files, allow = groups[0]
    assert (study_id, data_type, artifact_type, allow) == (5, "16S", "per_sample_FASTQ", {"s1"})
    assert samples == [("s1", "P1")]
    assert files == [("raw_forward_seqs", "per_sample_FASTQ", True, 10, "P1_R1.fastq.gz")]
    # the files query (over study_artifact) runs before the per-prep sample query
    assert "study_artifact" in calls[0][0]
    assert calls[0][1] == [[5]]


def test_study_groups_empty_when_no_study_ids_or_no_artifacts(fm):
    assert fm._study_groups([], {}) == []
    with patch.object(fm, "pooled_fetchall", return_value=[]):
        assert fm._study_groups([5], {}) == []


# ── to_tsv ───────────────────────────────────────────────────────────────────

def test_to_tsv_paired(fm):
    out = fm.to_tsv([("s1", "/f_R1.gz", "/f_R2.gz"), ("s2", "/g_R1.gz", None)], paired=True)
    lines = out.split("\n")
    assert lines[0] == "sample-id\tforward-absolute-filepath\treverse-absolute-filepath"
    assert lines[1] == "s1\t/f_R1.gz\t/f_R2.gz"
    assert lines[2] == "s2\t/g_R1.gz\t"          # missing reverse → empty field
    assert out.endswith("\n")


def test_to_tsv_single(fm):
    out = fm.to_tsv([("s1", "/f.gz", None)], paired=False)
    assert out == "sample-id\tabsolute-filepath\ns1\t/f.gz\n"


# ── route ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("fastq_manifest") / "test.db")
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = db_path
    for name in list(sys.modules):
        if (name == "run" or name.startswith("routes.") or name == "store"
                or name.startswith("store.") or name.startswith("helpers.")
                or "sql_store" in name):
            del sys.modules[name]
    stub_qiita_db_and_core()
    import run
    import config
    config.ALLOWED_ORIGINS = []
    config.SESSION_COOKIE_SECURE = False
    return run.app


@pytest.fixture
def client(_app):
    return _app.test_client()


@pytest.fixture
def logged_in(client, monkeypatch):
    import routes.auth_routes as auth_routes
    from helpers.qiita_client import WhoAmIResult

    monkeypatch.setattr(auth_routes, "whoami", lambda pat: WhoAmIResult(ok=True, identity={
        "principal_idx": 90002, "email": "fastq@test.local",
        "system_role": "user", "scopes": [], "profile_complete": True,
    }))
    resp = client.post("/api/auth/connect", json={"token": "qk_test"})
    assert resp.status_code == 200, resp.get_json()


@pytest.fixture
def fake_manifest(monkeypatch):
    import routes.artifact_routes as ar
    monkeypatch.setattr(ar, "is_study_public", lambda sid: sid != 99999)
    monkeypatch.setattr(ar, "fetch_manifest",
                        lambda sid, aid: (19685, [("s1", "/a/f_R1.fq.gz", "/a/f_R2.fq.gz")], True))


def test_route_returns_tsv_attachment(client, logged_in, fake_manifest):
    resp = client.get("/api/artifacts/233553/fastq-manifest?study_id=16326")
    assert resp.status_code == 200
    assert resp.mimetype == "text/tab-separated-values"
    assert resp.headers["Content-Disposition"] == "attachment; filename=manifest_prep19685_artifact233553.tsv"
    body = resp.get_data(as_text=True).split("\n")
    assert body[0] == "sample-id\tforward-absolute-filepath\treverse-absolute-filepath"
    assert body[1] == "s1\t/a/f_R1.fq.gz\t/a/f_R2.fq.gz"


def test_route_requires_study_id(client, logged_in, fake_manifest):
    assert client.get("/api/artifacts/233553/fastq-manifest").status_code == 400


def test_route_404_for_non_public_study(client, logged_in, fake_manifest):
    assert client.get("/api/artifacts/233553/fastq-manifest?study_id=99999").status_code == 404


def test_route_404_when_not_fastq_artifact(client, logged_in, monkeypatch):
    import routes.artifact_routes as ar
    monkeypatch.setattr(ar, "is_study_public", lambda sid: True)

    def _raise(sid, aid):
        raise ValueError("nope")
    monkeypatch.setattr(ar, "fetch_manifest", _raise)
    resp = client.get("/api/artifacts/1/fastq-manifest?study_id=1")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "nope"


def test_route_401_without_session(_app, fake_manifest):
    assert _app.test_client().get("/api/artifacts/233553/fastq-manifest?study_id=16326").status_code == 401
