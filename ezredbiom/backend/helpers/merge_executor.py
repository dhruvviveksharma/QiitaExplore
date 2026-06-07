"""Execute a BIOM merge job locally via subprocess (dev mode).

TODO (before merging to master): replace _run_local with SFTP+SSH pipeline
using paramiko — upload BIOMs to remote node, ssh exec, download result.
Same on_status interface; only the internals change.
"""

import csv
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

MERGE_CONDA_ENV = os.getenv("MERGE_CONDA_ENV", "qiita")
MERGE_RESULTS_DIR = os.getenv(
    "MERGE_RESULTS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "merge_results"),
)
_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "scripts", "remote_merge.py",
)

os.makedirs(MERGE_RESULTS_DIR, exist_ok=True)


def _write_merged_sample_metadata(jobdir: Path, workspace_snap: list) -> bool:
    """Fetch sample metadata for each study and write a combined TSV.

    Returns True if the file was written, False on any error.
    """
    try:
        from helpers.qiita_fetch import _fetch_full_sample_metadata
    except Exception as e:
        logger.warning("Could not import _fetch_full_sample_metadata: %s", e)
        return False

    all_rows = []
    all_keys = set()
    for entry in workspace_snap:
        try:
            rows = _fetch_full_sample_metadata(entry["study_id"], limit=9_999_999)
            sample_ids_filter = set(entry.get("sample_ids") or [])
            for r in rows:
                if not sample_ids_filter or r["sample_id"] in sample_ids_filter:
                    all_keys.update(r["fields"].keys())
                    all_rows.append(r)
        except Exception as e:
            logger.warning("Could not fetch sample metadata for study %s: %s", entry["study_id"], e)

    if not all_rows:
        return False

    cols = sorted(all_keys)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    writer.writerow(["#SampleID"] + cols)
    for r in all_rows:
        writer.writerow([r["sample_id"]] + [r["fields"].get(c, "not applicable") for c in cols])

    (jobdir / "sample_metadata.tsv").write_text(buf.getvalue())
    return True


def run_merge_job(job_id: str, workspace_snap: list, on_status):
    """Blocking function — meant to run inside a ThreadPoolExecutor worker.

    workspace_snap: list of {study_id, artifact_path, sample_ids (list|null)}
    on_status(status, error=None, result_path=None): writes to DB
    """
    jobdir = Path(tempfile.mkdtemp(prefix=f"merge_{job_id}_", dir=MERGE_RESULTS_DIR))
    try:
        on_status("running")

        # Copy BIOM files into the temp jobdir
        _base = os.getenv("QIITA_BASE_DATA_DIR", "").rstrip("/")
        for entry in workspace_snap:
            src = entry["artifact_path"]
            if _base and not os.path.isabs(src):
                src = f"{_base}/{src}"
            dst = jobdir / f"{entry['study_id']}.biom"
            shutil.copy2(src, dst)
            entry["biom_file"] = f"{entry['study_id']}.biom"

        # Write manifest
        manifest = {"job_id": job_id, "studies": workspace_snap}
        (jobdir / "manifest.json").write_text(json.dumps(manifest))

        # Write merged sample metadata (best-effort — failure won't abort the merge)
        _write_merged_sample_metadata(jobdir, workspace_snap)

        # Run merge script in conda env
        cmd = [
            "conda", "run", "--no-capture-output",
            "-n", MERGE_CONDA_ENV,
            "python", _SCRIPT_PATH, str(jobdir),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.stdout:
            for line in result.stdout.splitlines():
                logger.info("[merge:%s] %s", job_id, line)
        if result.stderr:
            for line in result.stderr.splitlines():
                logger.warning("[merge:%s] %s", job_id, line)

        result.check_returncode()

        # Move result bundle to final location
        final_path = os.path.join(MERGE_RESULTS_DIR, f"{job_id}.tar.gz")
        shutil.move(str(jobdir / "result.tar.gz"), final_path)

        on_status("done", result_path=final_path)

    except Exception as e:
        logger.exception("[merge:%s] job failed", job_id)
        on_status("failed", error=str(e))
    finally:
        if jobdir.exists():
            shutil.rmtree(jobdir, ignore_errors=True)
