#!/usr/bin/env python
"""Prepare per-sample FASTQ data for QIIME2 analysis.

Two input modes:
  --study-id  : resolve paths directly from Qiita PostgreSQL (qmounts)
  --fastq-dir : use an already-downloaded study_raw_data directory
"""
import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd


# ── 1. PostgreSQL queries (qmounts mode) ─────────────────────────────────────

def fetch_fastq_artifacts(study_id: int) -> list:
    """Return FASTQ file records from Qiita PostgreSQL.

    Each record: {prep_template_id, filepath_type, full_path}.
    """
    from qiita_db.sql_connection import TRN
    sql = """
        SELECT pt.prep_template_id,
               ft.filepath_type,
               dd.mountpoint || '/' || a.artifact_id || '/' || f.filepath AS full_path
        FROM qiita.study_prep_template spt
        JOIN qiita.prep_template pt        ON spt.prep_template_id = pt.prep_template_id
        JOIN qiita.preparation_artifact pa ON pt.prep_template_id  = pa.prep_template_id
        JOIN qiita.artifact a              ON pa.artifact_id        = a.artifact_id
        JOIN qiita.artifact_type at        ON a.artifact_type_id    = at.artifact_type_id
        JOIN qiita.artifact_filepath af    ON a.artifact_id         = af.artifact_id
        JOIN qiita.filepath f              ON af.filepath_id        = f.filepath_id
        JOIN qiita.filepath_type ft        ON f.filepath_type_id   = ft.filepath_type_id
        JOIN qiita.data_directory dd       ON f.data_directory_id  = dd.data_directory_id
        WHERE spt.study_id = %s AND at.artifact_type = 'per_sample_FASTQ'
        ORDER BY pt.prep_template_id, f.filepath
    """
    try:
        with TRN:
            TRN.add(sql, [int(study_id)])
            rows = TRN.execute_fetchindex()
        return [{"prep_template_id": r[0], "filepath_type": r[1], "full_path": r[2]} for r in rows]
    except Exception as e:
        print(f"ERROR: PostgreSQL query failed: {e}", file=sys.stderr)
        sys.exit(1)


def fetch_prep_samples(prep_template_id: int) -> pd.DataFrame:
    """Return sample_id + run_prefix from the dynamic prep template table."""
    from qiita_db.sql_connection import TRN
    try:
        with TRN:
            TRN.add(f"SELECT sample_id, run_prefix FROM qiita.prep_{prep_template_id}", [])
            rows = TRN.execute_fetchindex()
        return pd.DataFrame(rows, columns=["sample_id", "run_prefix"])
    except Exception as e:
        print(f"  WARN: could not fetch prep {prep_template_id} samples: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["sample_id", "run_prefix"])


# ── 2. Parse local files (zip mode) ──────────────────────────────────────────

def parse_mapping_file(mapping_path: str) -> pd.DataFrame:
    """Read a Qiita mapping file / prep template TSV."""
    return pd.read_csv(mapping_path, sep="\t")


def parse_manifest(index_html_path: str) -> dict:
    """Parse index.html MD5 table. Returns {filename: {md5, file_type, reads}}."""
    tables = pd.read_html(index_html_path)
    if not tables:
        return {}
    df = tables[0]
    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    out = {}
    for _, row in df.iterrows():
        fname = str(row.get("filename", row.get("file", ""))).strip()
        if fname and fname != "nan":
            out[fname] = {
                "md5": str(row.get("md5", "")).strip(),
                "file_type": str(row.get("file_type", "")).strip(),
                "reads": row.get("reads"),
            }
    return out


# ── 3. Checksum verification ──────────────────────────────────────────────────

def verify_checksums(fastq_dir: str, manifest: dict) -> list:
    """MD5-check .fastq.gz files against manifest. Returns list of failed filenames."""
    failed = []
    for fname, meta in manifest.items():
        path = Path(fastq_dir) / fname
        if not path.exists():
            print(f"  MISSING  {fname}")
            failed.append(fname)
            continue
        digest = hashlib.md5(path.read_bytes()).hexdigest()
        expected = meta.get("md5", "").lower()
        if expected and digest != expected:
            print(f"  FAIL     {fname}  got={digest}  expected={expected}")
            failed.append(fname)
        else:
            print(f"  OK       {fname}")
    return failed


# ── 4. Build QIIME2 manifest ──────────────────────────────────────────────────

def build_qiime2_manifest_from_mapping(mapping_df: pd.DataFrame, fastq_dir: str) -> pd.DataFrame:
    """Build QIIME2 PairedEndFastqManifestPhred33V2 from mapping file + directory."""
    base = Path(fastq_dir).resolve()
    rows = []
    for _, row in mapping_df.iterrows():
        sample = str(row["sample_name"]).strip()
        prefix = str(row["run_prefix"]).strip()
        fwd = base / f"{prefix}_R1.fastq.gz"
        rev = base / f"{prefix}_R2.fastq.gz"
        if not fwd.exists() or not rev.exists():
            print(f"  WARN: missing files for {sample} ({prefix})", file=sys.stderr)
            continue
        rows.append({
            "sample-id": sample,
            "forward-absolute-filepath": str(fwd),
            "reverse-absolute-filepath": str(rev),
        })
    return pd.DataFrame(rows, columns=["sample-id", "forward-absolute-filepath", "reverse-absolute-filepath"])


def build_qiime2_manifest_from_db(artifacts: list, prep_template_id: int) -> pd.DataFrame:
    """Build QIIME2 manifest from DB-fetched artifact paths + prep template samples."""
    prep_artifacts = [r for r in artifacts if r["prep_template_id"] == prep_template_id]
    fwd_paths = [r["full_path"] for r in prep_artifacts if "forward" in r["filepath_type"]]
    rev_paths = [r["full_path"] for r in prep_artifacts if "reverse" in r["filepath_type"]]

    samples_df = fetch_prep_samples(prep_template_id)
    if samples_df.empty:
        # Fallback: infer sample-id from filename stem
        rows = []
        for fwd in sorted(fwd_paths):
            stem = Path(fwd).name.replace("_R1.fastq.gz", "").replace("_R1_001.fastq.gz", "")
            rev = fwd.replace("_R1", "_R2")
            rows.append({"sample-id": stem, "forward-absolute-filepath": fwd, "reverse-absolute-filepath": rev})
        return pd.DataFrame(rows, columns=["sample-id", "forward-absolute-filepath", "reverse-absolute-filepath"])

    rows = []
    for _, row in samples_df.iterrows():
        prefix = str(row["run_prefix"]).strip()
        fwd_match = next((p for p in fwd_paths if prefix in Path(p).name), None)
        rev_match = next((p for p in rev_paths if prefix in Path(p).name), None)
        if not fwd_match or not rev_match:
            print(f"  WARN: no files for sample {row['sample_id']} (prefix={prefix})", file=sys.stderr)
            continue
        rows.append({
            "sample-id": str(row["sample_id"]),
            "forward-absolute-filepath": fwd_match,
            "reverse-absolute-filepath": rev_match,
        })
    return pd.DataFrame(rows, columns=["sample-id", "forward-absolute-filepath", "reverse-absolute-filepath"])


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--study-id", type=int, metavar="ID",
                     help="Qiita study ID (resolves via PostgreSQL / qmounts)")
    src.add_argument("--fastq-dir", metavar="PATH",
                     help="Path to extracted study_raw_data directory")
    ap.add_argument("--output", required=True, metavar="PATH", help="Output directory")
    ap.add_argument("--verify-checksums", action="store_true",
                    help="MD5-check FASTQ files against index.html (zip mode only)")
    ap.add_argument("--write-manifest", action="store_true",
                    help="Write QIIME2 PairedEndFastqManifestPhred33V2 CSV")
    args = ap.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── qmounts path ──────────────────────────────────────────────────────────
    if args.study_id:
        print(f"Fetching per_sample_FASTQ artifacts for study {args.study_id} …")
        artifacts = fetch_fastq_artifacts(args.study_id)
        if not artifacts:
            print("No per_sample_FASTQ artifacts found.", file=sys.stderr)
            sys.exit(1)
        prep_ids = sorted({r["prep_template_id"] for r in artifacts})
        print(f"Found {len(artifacts)} files across {len(prep_ids)} prep(s): {prep_ids}")
        for r in artifacts:
            print(f"  prep={r['prep_template_id']}  [{r['filepath_type']}]  {r['full_path']}")

        if args.write_manifest:
            for pid in prep_ids:
                print(f"\nBuilding manifest for prep {pid} …")
                mf = build_qiime2_manifest_from_db(artifacts, pid)
                out_path = out_dir / f"manifest_prep{pid}.csv"
                mf.to_csv(out_path, index=False)
                print(f"Wrote {len(mf)} samples → {out_path}")

    # ── zip / directory path ──────────────────────────────────────────────────
    else:
        fastq_root = Path(args.fastq_dir)
        per_sample_dir = fastq_root / "per_sample_FASTQ"
        mapping_dir = fastq_root / "mapping_files"
        if not per_sample_dir.exists():
            per_sample_dir = fastq_root

        prep_dirs = sorted([d for d in per_sample_dir.iterdir() if d.is_dir()]) \
            if per_sample_dir.is_dir() and any(per_sample_dir.iterdir()) \
            else [per_sample_dir]

        for prep_dir in prep_dirs:
            prep_id = prep_dir.name
            n_files = len(list(prep_dir.glob("*.fastq.gz")))
            print(f"\nPrep: {prep_id}  ({n_files} fastq.gz files)")

            if args.verify_checksums:
                index_html = prep_dir / "index.html"
                if not index_html.exists():
                    print(f"  WARN: no index.html in {prep_dir}", file=sys.stderr)
                else:
                    print("Verifying checksums …")
                    manifest = parse_manifest(str(index_html))
                    failed = verify_checksums(str(prep_dir), manifest)
                    if failed:
                        print(f"\nFAILED ({len(failed)}): {failed}")
                    else:
                        print(f"All {len(manifest)} checksums OK")

            if args.write_manifest:
                candidates = list(mapping_dir.glob(f"{prep_id}*")) if mapping_dir.exists() else []
                if not candidates:
                    candidates = list(fastq_root.glob(f"*{prep_id}*mapping*"))
                if not candidates:
                    print(f"  WARN: no mapping file found for prep {prep_id}", file=sys.stderr)
                    continue
                mapping_df = parse_mapping_file(str(candidates[0]))
                mf = build_qiime2_manifest_from_mapping(mapping_df, str(prep_dir))
                out_path = out_dir / f"manifest_prep{prep_id}.csv"
                mf.to_csv(out_path, index=False)
                print(f"Wrote {len(mf)} samples → {out_path}")


if __name__ == "__main__":
    main()
