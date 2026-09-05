"""Tests for the per-sample FASTQ manifest (helpers/fastq_manifest.py + its route).

The matcher/serializer are pure Python and take base_dir explicitly, so they
run without Postgres. The route test reuses the test_stream_routes app
pattern and fakes the two helper boundaries.
"""
import os
import sys

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
