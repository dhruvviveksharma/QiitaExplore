"""QIIME2 V2 FASTQ manifest for per_sample_FASTQ artifacts.

Qiita stores no sample↔file link; the mapping is a run_prefix substring match
against the filename, tried longest-prefix-first (mirrors
qiita_pet/handlers/api_proxy/studies.py). Absolute paths follow
qiita_db.util._path_builder: the artifact_id segment exists only when
data_directory.subdirectory is true (legacy raw_data files sit flat and were
renamed "{obj_id}_{basename}", which is why startswith() would miss them).

Two entry points share one SQL block and one row builder so path logic can't
drift: fetch_manifest (a single artifact, used by the study modal) and
fetch_aggregate_manifest (every per_sample_FASTQ artifact across a Sample
Aggregation's studies).
"""

import csv
import io
import logging

from helpers.artifact_graph import _BASE
from helpers.pg_pool import pooled_fetchall

logger = logging.getLogger(__name__)

_FILES_SELECT = """
SELECT sa.study_id, a.artifact_id, pa.prep_template_id, ft.filepath_type,
       dd.mountpoint, dd.subdirectory, f.filepath
FROM qiita.study_artifact sa
JOIN qiita.artifact a               ON sa.artifact_id = a.artifact_id
JOIN qiita.artifact_type at         ON a.artifact_type_id = at.artifact_type_id
JOIN qiita.preparation_artifact pa  ON pa.artifact_id = a.artifact_id
JOIN qiita.artifact_filepath af     ON af.artifact_id = a.artifact_id
JOIN qiita.filepath f               ON af.filepath_id = f.filepath_id
JOIN qiita.filepath_type ft         ON f.filepath_type_id = ft.filepath_type_id
JOIN qiita.data_directory dd        ON f.data_directory_id = dd.data_directory_id
WHERE at.artifact_type = 'per_sample_FASTQ'
  AND ft.filepath_type IN ('raw_forward_seqs', 'raw_reverse_seqs')
"""
_ARTIFACT_FILES_SQL = _FILES_SELECT + "  AND sa.study_id = %s AND a.artifact_id = %s\n"
_STUDIES_FILES_SQL = _FILES_SELECT + "  AND sa.study_id = ANY(%s)\n"

_COUNT_SQL = """
SELECT COUNT(*)
FROM qiita.study_artifact sa
JOIN qiita.artifact a       ON sa.artifact_id = a.artifact_id
JOIN qiita.artifact_type at ON a.artifact_type_id = at.artifact_type_id
WHERE sa.study_id = %s AND at.artifact_type = 'per_sample_FASTQ'
"""

# pid comes from the query above (an int), never from request input — same
# pattern as sample_{study_id} in routes/artifact_routes.py.
_SAMPLES_SQL = """
SELECT sample_id, sample_values->>'run_prefix'
FROM qiita.prep_{pid}
WHERE sample_id <> 'qiita_sample_column_names'
"""

_PAIRED_HEADER = ["sample-id", "forward-absolute-filepath", "reverse-absolute-filepath"]
_SINGLE_HEADER = ["sample-id", "absolute-filepath"]


def _claim(pool, prefix):
    """Pop and return the path of the first pooled (filename, path) containing prefix."""
    for i, (filename, path) in enumerate(pool):
        if prefix in filename:
            del pool[i]
            return path
    return None


def build_manifest_rows(samples, files, base_dir):
    """samples: [(sample_id, run_prefix)]; files: [(filepath_type, mountpoint, subdirectory, artifact_id, filename)].

    Returns (rows, paired) with rows = [(sample_id, fwd_path, rev_path_or_None)] sorted by sample_id.
    Samples with no run_prefix or no matching forward file are omitted.
    """
    fwd, rev = [], []
    for ftype, mountpoint, subdirectory, artifact_id, filename in files:
        rel = f"{mountpoint}/{artifact_id}/{filename}" if subdirectory else f"{mountpoint}/{filename}"
        (fwd if ftype == "raw_forward_seqs" else rev).append((filename, f"{base_dir}/{rel}"))
    paired = bool(rev)

    rows = []
    # Longest prefix first so '1002' claims its files before '100' can.
    for sample_id, prefix in sorted(samples, key=lambda s: len(s[1] or ""), reverse=True):
        if not prefix:
            continue
        f = _claim(fwd, prefix)
        if f is None:
            continue
        rows.append((sample_id, f, _claim(rev, prefix) if paired else None))
    rows.sort()
    return rows, paired


def build_aggregate_rows(groups, base_dir):
    """groups: [(samples, files)] in deterministic order → (rows, paired).

    paired if any group is paired (single-end rows then carry a None reverse,
    which to_tsv writes as an empty cell). A sample_id already emitted by an
    earlier group wins; later duplicates (e.g. the same sample in a 16S and a
    shotgun prep) are dropped, since a QIIME2 manifest needs unique sample-ids.
    """
    rows, seen, paired, dropped = [], set(), False, 0
    for samples, files in groups:
        g_rows, g_paired = build_manifest_rows(samples, files, base_dir)
        paired = paired or g_paired
        for row in g_rows:
            if row[0] in seen:
                dropped += 1
                continue
            seen.add(row[0])
            rows.append(row)
    if dropped:
        logger.warning("aggregate manifest: dropped %d duplicate sample-id row(s)", dropped)
    rows.sort()
    return rows, paired


def to_tsv(rows, paired):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    w.writerow(_PAIRED_HEADER if paired else _SINGLE_HEADER)
    for sample_id, fwd, rev in rows:
        w.writerow([sample_id, fwd, rev or ""] if paired else [sample_id, fwd])
    return buf.getvalue()


def _files(frows):
    """_FILES_SELECT rows → build_manifest_rows' files shape."""
    return [(r[3], r[4], r[5], r[1], r[6]) for r in frows]


def fetch_manifest(study_id, artifact_id):
    """Return (prep_template_id, rows, paired). Raises ValueError if the artifact
    is not a per_sample_FASTQ artifact of this study."""
    if not _BASE:
        raise RuntimeError("QIITA_BASE_DATA_DIR is not set; manifest paths would be relative")
    frows = pooled_fetchall(_ARTIFACT_FILES_SQL, [int(study_id), int(artifact_id)])
    if not frows:
        raise ValueError(f"Artifact {artifact_id} is not a per_sample_FASTQ artifact of study {study_id}")
    pid = frows[0][2]
    samples = pooled_fetchall(_SAMPLES_SQL.format(pid=int(pid)))
    rows, paired = build_manifest_rows(samples, _files(frows), _BASE)
    return pid, rows, paired


def count_fastq_artifacts(study_id):
    """Number of per_sample_FASTQ artifacts in a study (snapshotted onto
    aggregation_studies when the study is added to an aggregation)."""
    return int(pooled_fetchall(_COUNT_SQL, [int(study_id)])[0][0])


def fetch_aggregate_manifest(study_ids):
    """Return (rows, paired) over every per_sample_FASTQ artifact of the given
    studies. Raises ValueError if none of them has one."""
    if not _BASE:
        raise RuntimeError("QIITA_BASE_DATA_DIR is not set; manifest paths would be relative")
    frows = pooled_fetchall(_STUDIES_FILES_SQL, [[int(s) for s in study_ids]])
    if not frows:
        raise ValueError("No per_sample_FASTQ artifacts in these studies")
    # Bucket by (study_id, prep_id, artifact_id); iterate sorted so duplicate
    # handling in build_aggregate_rows is deterministic.
    by_artifact = {}
    for r in frows:
        by_artifact.setdefault((r[0], r[2], r[1]), []).append(r)
    samples_by_prep = {}  # a prep can own several artifacts; fetch its samples once
    groups = []
    for key in sorted(by_artifact):
        pid = key[1]
        if pid not in samples_by_prep:
            samples_by_prep[pid] = pooled_fetchall(_SAMPLES_SQL.format(pid=int(pid)))
        groups.append((samples_by_prep[pid], _files(by_artifact[key])))
    return build_aggregate_rows(groups, _BASE)
