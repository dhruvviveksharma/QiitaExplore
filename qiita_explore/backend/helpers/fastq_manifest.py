"""QIIME2 V2 FASTQ manifest for a per_sample_FASTQ artifact.

Qiita stores no sample↔file link; the mapping is a run_prefix substring match
against the filename, tried longest-prefix-first (mirrors
qiita_pet/handlers/api_proxy/studies.py). Absolute paths follow
qiita_db.util._path_builder: the artifact_id segment exists only when
data_directory.subdirectory is true (legacy raw_data files sit flat and were
renamed "{obj_id}_{basename}", which is why startswith() would miss them).
"""

import csv
import io

from helpers.artifact_graph import _BASE
from helpers.pg_pool import pooled_fetchall

_ARTIFACT_FILES_SQL = """
SELECT pa.prep_template_id, ft.filepath_type, dd.mountpoint, dd.subdirectory, f.filepath
FROM qiita.study_artifact sa
JOIN qiita.artifact a               ON sa.artifact_id = a.artifact_id
JOIN qiita.artifact_type at         ON a.artifact_type_id = at.artifact_type_id
JOIN qiita.preparation_artifact pa  ON pa.artifact_id = a.artifact_id
JOIN qiita.artifact_filepath af     ON af.artifact_id = a.artifact_id
JOIN qiita.filepath f               ON af.filepath_id = f.filepath_id
JOIN qiita.filepath_type ft         ON f.filepath_type_id = ft.filepath_type_id
JOIN qiita.data_directory dd        ON f.data_directory_id = dd.data_directory_id
WHERE sa.study_id = %s AND a.artifact_id = %s
  AND at.artifact_type = 'per_sample_FASTQ'
  AND ft.filepath_type IN ('raw_forward_seqs', 'raw_reverse_seqs')
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


def to_tsv(rows, paired):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    w.writerow(_PAIRED_HEADER if paired else _SINGLE_HEADER)
    for sample_id, fwd, rev in rows:
        w.writerow([sample_id, fwd, rev or ""] if paired else [sample_id, fwd])
    return buf.getvalue()


def fetch_manifest(study_id, artifact_id):
    """Return (prep_template_id, rows, paired). Raises ValueError if the artifact
    is not a per_sample_FASTQ artifact of this study."""
    if not _BASE:
        raise RuntimeError("QIITA_BASE_DATA_DIR is not set; manifest paths would be relative")
    frows = pooled_fetchall(_ARTIFACT_FILES_SQL, [int(study_id), int(artifact_id)])
    if not frows:
        raise ValueError(f"Artifact {artifact_id} is not a per_sample_FASTQ artifact of study {study_id}")
    pid = frows[0][0]
    files = [(r[1], r[2], r[3], artifact_id, r[4]) for r in frows]
    samples = pooled_fetchall(_SAMPLES_SQL.format(pid=int(pid)))
    rows, paired = build_manifest_rows(samples, files, _BASE)
    return pid, rows, paired
