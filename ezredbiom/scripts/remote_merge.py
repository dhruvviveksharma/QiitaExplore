#!/usr/bin/env python
"""Merge BIOM tables from multiple studies into a single combined table.

Usage: python remote_merge.py <jobdir>

Expects <jobdir>/manifest.json:
{
  "job_id": "...",
  "studies": [
    {"study_id": 77, "biom_file": "77.biom", "sample_ids": null},
    {"study_id": 88, "biom_file": "88.biom", "sample_ids": ["s1", "s2"]}
  ]
}

Writes to <jobdir>/:
  merged.biom       — HDF5 BIOM table (union of all features)
  provenance.json   — merge metadata
  result.tar.gz     — bundle of both files

Exit 0 on success, 1 on failure.
"""

import csv
import datetime
import json
import sys
import tarfile
import traceback
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: remote_merge.py <jobdir>", file=sys.stderr)
        sys.exit(1)

    jobdir = Path(sys.argv[1])
    manifest = json.loads((jobdir / "manifest.json").read_text())

    from biom import load_table
    from biom.util import biom_open

    tables = []
    for entry in manifest["studies"]:
        biom_path = jobdir / entry["biom_file"]
        t = load_table(str(biom_path))

        sample_ids = entry.get("sample_ids")
        if sample_ids:
            ids_set = set(sample_ids)
            t.filter(lambda val, id_, md: id_ in ids_set, axis="sample", inplace=True)

        if t.shape[1] == 0:
            raise ValueError(
                f"Study {entry['study_id']}: no samples remain after filter. "
                f"Requested: {sample_ids[:5] if sample_ids else 'all'}"
            )

        tables.append(t)

    if not tables:
        raise ValueError("No tables to merge")

    merged = tables[0]
    for t in tables[1:]:
        merged = merged.merge(t)

    merged_path = jobdir / "merged.biom"
    with biom_open(str(merged_path), "w") as f:
        merged.to_hdf5(f, "remote_merge")

    meta_path = jobdir / "sample_metadata.tsv"
    if meta_path.exists():
        merged_ids = set(merged.ids(axis="sample"))
        with open(meta_path, newline="") as fh:
            all_rows = list(csv.reader(fh, delimiter="\t"))
        header = all_rows[:1]
        filtered = [r for r in all_rows[1:] if r and r[0] in merged_ids]
        with open(meta_path, "w", newline="") as fh:
            csv.writer(fh, delimiter="\t", lineterminator="\n").writerows(header + filtered)

    prov = {
        "job_id": manifest["job_id"],
        "study_ids": [e["study_id"] for e in manifest["studies"]],
        "feature_count": merged.shape[0],
        "sample_count": merged.shape[1],
        "merged_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    prov_path = jobdir / "provenance.json"
    prov_path.write_text(json.dumps(prov, indent=2))

    meta_path = jobdir / "sample_metadata.tsv"
    prov["includes_sample_metadata"] = meta_path.exists()
    prov_path.write_text(json.dumps(prov, indent=2))

    result_path = jobdir / "result.tar.gz"
    with tarfile.open(str(result_path), "w:gz") as tar:
        tar.add(str(merged_path), arcname="merged.biom")
        tar.add(str(prov_path), arcname="provenance.json")
        if meta_path.exists():
            tar.add(str(meta_path), arcname="sample_metadata.tsv")

    print(f"DONE — {merged.shape[0]} features × {merged.shape[1]} samples")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)
