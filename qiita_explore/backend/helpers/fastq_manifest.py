"""Per-sample sequence-file paths for Qiita artifacts.

Qiita stores no sample↔file link; the mapping is a run_prefix substring match
against the filename, tried longest-prefix-first (mirrors
qiita_pet/handlers/api_proxy/studies.py). Absolute paths follow
qiita_db.util._path_builder: the artifact_id segment exists only when
data_directory.subdirectory is true (legacy raw_data files sit flat and were
renamed "{obj_id}_{basename}", which is why startswith() would miss them).

Two entry points share one JOIN block and one row builder so path logic can't
drift: fetch_manifest (a single per_sample_FASTQ artifact → QIIME2 V2 manifest,
used by the study modal) and fetch_aggregate_csv_rows (every per-sample
sequence artifact — per_sample_FASTQ fwd/rev and FASTA raw_fasta — across a
Sample Aggregation's studies, restricted to the checked samples).

_study_groups is the shared group-assembly step behind both
fetch_aggregate_csv_rows (checked samples → CSV rows) and get_sample_files
(every sample of one study → which have a file at all, for the sample
table's FASTQ/FASTA columns and files-first ordering).
"""

import csv
import io
import json
import time

from helpers.artifact_graph import _BASE
from helpers.pg_pool import pooled_fetchall

_FILES_FROM = """
SELECT sa.study_id, a.artifact_id, pa.prep_template_id, ft.filepath_type,
       dd.mountpoint, dd.subdirectory, f.filepath, dt.data_type, at.artifact_type
FROM qiita.study_artifact sa
JOIN qiita.artifact a               ON sa.artifact_id = a.artifact_id
JOIN qiita.artifact_type at         ON a.artifact_type_id = at.artifact_type_id
JOIN qiita.preparation_artifact pa  ON pa.artifact_id = a.artifact_id
JOIN qiita.prep_template pt         ON pa.prep_template_id = pt.prep_template_id
JOIN qiita.data_type dt             ON pt.data_type_id = dt.data_type_id
JOIN qiita.artifact_filepath af     ON af.artifact_id = a.artifact_id
JOIN qiita.filepath f               ON af.filepath_id = f.filepath_id
JOIN qiita.filepath_type ft         ON f.filepath_type_id = ft.filepath_type_id
JOIN qiita.data_directory dd        ON f.data_directory_id = dd.data_directory_id
"""
# The study-modal manifest is QIIME2-shaped and FASTQ-only.
_WHERE_FASTQ = """WHERE at.artifact_type = 'per_sample_FASTQ'
  AND ft.filepath_type IN ('raw_forward_seqs', 'raw_reverse_seqs')
"""
# The aggregate CSV / availability map also take Qiita's per-sample FASTA
# uploads (artifact type FASTA, one raw_fasta per sample). raw_qual is not
# sequence and is skipped.
_WHERE_SEQ = """WHERE at.artifact_type IN ('per_sample_FASTQ', 'FASTA')
  AND ft.filepath_type IN ('raw_forward_seqs', 'raw_reverse_seqs', 'raw_fasta')
"""
_ARTIFACT_FILES_SQL = _FILES_FROM + _WHERE_FASTQ + "  AND sa.study_id = %s AND a.artifact_id = %s\n"
_STUDIES_FILES_SQL = _FILES_FROM + _WHERE_SEQ + "  AND sa.study_id = ANY(%s)\n"

_COUNT_SQL = """
SELECT COUNT(*)
FROM qiita.study_artifact sa
JOIN qiita.artifact a       ON sa.artifact_id = a.artifact_id
JOIN qiita.artifact_type at ON a.artifact_type_id = at.artifact_type_id
WHERE sa.study_id = %s AND at.artifact_type IN ('per_sample_FASTQ', 'FASTA')
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
CSV_HEADER = ["study_id", "sample_id", "file_path_in_qmounts", "data_type", "file_type"]

# Availability map (get_sample_files) is cached two ways: a per-worker memo
# (cheap re-checks within a request burst) backed by a 6h SQLite row
# (study_detail_cache.sample_files_json, shared across workers/restarts).
_SAMPLE_FILES_MEMO_TTL_SECONDS = 600
_sample_files_memo = {}  # study_id -> (fetched_at_epoch, {sample_id: [fastq, fasta]}); tests clear it


def _claim(pool, prefix):
    """Pop and return the path of the first pooled (filename, path) containing prefix."""
    for i, (filename, path) in enumerate(pool):
        if prefix in filename:
            del pool[i]
            return path
    return None


def build_manifest_rows(samples, files, base_dir):
    """samples: [(sample_id, run_prefix)]; files: [(filepath_type, mountpoint, subdirectory, artifact_id, filename)].

    Returns (rows, paired) with rows = [(sample_id, fwd_path, rev_path_or_None)]
    sorted by sample_id. Anything that isn't a reverse read (raw_forward_seqs,
    or a FASTA artifact's raw_fasta) is the sample's forward file. Samples with
    no run_prefix or no matching forward file are omitted.
    """
    fwd, rev = [], []
    for ftype, mountpoint, subdirectory, artifact_id, filename in files:
        rel = f"{mountpoint}/{artifact_id}/{filename}" if subdirectory else f"{mountpoint}/{filename}"
        (rev if ftype == "raw_reverse_seqs" else fwd).append((filename, f"{base_dir}/{rel}"))
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


def build_csv_rows(groups, base_dir):
    """groups: [(study_id, data_type, artifact_type, samples, files, allow)],
    one per artifact. `samples` is the prep's FULL (sample_id, run_prefix)
    list — longest-prefix-first claiming needs every sample present, or a
    selected '8B4' would take '8B4ABX_R1.fastq.gz' once unselected '8B4ABX'
    were filtered away. `allow` is the set of checked sample ids; only their
    rows are emitted.

    Returns sorted, de-duplicated (study_id, sample_id, path, data_type,
    file_type) rows — one per file: a paired sample yields two rows and a
    sample in two preps yields rows for each data type.
    """
    out = set()
    for study_id, data_type, artifact_type, samples, files, allow in groups:
        fwd_type = "raw_fasta" if artifact_type == "FASTA" else "raw_forward_seqs"
        rows, _ = build_manifest_rows(samples, files, base_dir)
        for sample_id, fwd, rev in rows:
            if sample_id not in allow:
                continue
            out.add((study_id, sample_id, fwd, data_type, fwd_type))
            if rev:
                out.add((study_id, sample_id, rev, data_type, "raw_reverse_seqs"))
    return sorted(out)


def summarize_sample_files(groups):
    """groups: [(study_id, data_type, artifact_type, samples, files, allow)] —
    `allow` is ignored (availability considers every sample of the study, not
    just checked ones). Paths are irrelevant here too, only whether a sample
    resolves to a file, so build_manifest_rows is run with an empty base_dir.

    Returns {sample_id: [fastq, fasta]}: fastq is 2 (paired), 1 (single), or 0;
    fasta is 1 or 0 — the max/OR across every artifact of the study. Only
    samples that resolve to at least one file appear."""
    out = {}
    for _study_id, _data_type, artifact_type, samples, files, _allow in groups:
        rows, paired = build_manifest_rows(samples, files, "")
        is_fasta = artifact_type == "FASTA"
        for sample_id, _fwd, rev in rows:
            cur = out.setdefault(sample_id, [0, 0])
            if is_fasta:
                cur[1] = 1
            else:
                cur[0] = max(cur[0], 2 if rev else 1)
    return out


def to_tsv(rows, paired):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    w.writerow(_PAIRED_HEADER if paired else _SINGLE_HEADER)
    for sample_id, fwd, rev in rows:
        w.writerow([sample_id, fwd, rev or ""] if paired else [sample_id, fwd])
    return buf.getvalue()


def to_csv(rows, spreadsheet_safe=False):
    """spreadsheet_safe wraps sample_id as ="..." so Excel / Numbers / LibreOffice
    keep ids like "10317.000001062" as text instead of parsing (and rounding)
    them as a float — which otherwise displays indistinguishably from the
    study_id column. The plain CSV (default) stays clean for pandas/scripts."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_HEADER)
    if spreadsheet_safe:
        for study_id, sample_id, path, data_type, file_type in rows:
            w.writerow([study_id, f'="{sample_id}"', path, data_type, file_type])
    else:
        w.writerows(rows)
    return buf.getvalue()


def _files(frows):
    """_FILES_FROM rows → build_manifest_rows' files shape."""
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
    """Number of per-sample sequence artifacts (per_sample_FASTQ or FASTA) in a
    study — snapshotted onto aggregation_studies when the study is added."""
    return int(pooled_fetchall(_COUNT_SQL, [int(study_id)])[0][0])


def _study_groups(study_ids, selected):
    """Every per-sample sequence artifact (per_sample_FASTQ + FASTA) of
    study_ids, bucketed into (study_id, data_type, artifact_type, samples,
    files, allow) groups — one per artifact, `samples` the prep's FULL
    (sample_id, run_prefix) list, `allow` = selected.get(study_id, set()).
    [] when study_ids is empty or none of them has such an artifact."""
    if not study_ids:
        return []
    frows = pooled_fetchall(_STUDIES_FILES_SQL, [study_ids])
    if not frows:
        return []
    # Bucket by (study_id, prep_id, artifact_id); iterate sorted for determinism.
    by_artifact = {}
    for r in frows:
        by_artifact.setdefault((r[0], r[2], r[1]), []).append(r)
    samples_by_prep = {}  # a prep can own several artifacts; fetch its samples once
    groups = []
    for key in sorted(by_artifact):
        study_id, pid, _artifact_id = key
        if pid not in samples_by_prep:
            samples_by_prep[pid] = pooled_fetchall(_SAMPLES_SQL.format(pid=int(pid)))
        first = by_artifact[key][0]
        groups.append((study_id, first[7], first[8], samples_by_prep[pid],
                       _files(by_artifact[key]), selected.get(study_id, set())))
    return groups


def fetch_aggregate_csv_rows(selected):
    """selected: {study_id: {sample_id, ...}} — empty sets are ignored. Returns
    build_csv_rows over every per-sample sequence artifact of those studies.
    Raises ValueError when no such artifact exists or no checked sample
    resolves to a file."""
    if not _BASE:
        raise RuntimeError("QIITA_BASE_DATA_DIR is not set; export paths would be relative")
    study_ids = sorted(int(s) for s, ids in selected.items() if ids)
    groups = _study_groups(study_ids, selected)
    if not groups:
        raise ValueError("No per-sample sequence artifacts in the selected studies")
    rows = build_csv_rows(groups, _BASE)
    if not rows:
        raise ValueError("None of the selected samples has a per-sample sequence file")
    return rows


def compute_sample_files(study_id):
    """{sample_id: [fastq, fasta]} for every sample of one study that resolves
    to at least one file — a live Postgres computation, no cache. See
    get_sample_files for the cached entry point actually used by routes."""
    return summarize_sample_files(_study_groups([int(study_id)], {}))


def get_sample_files(study_id):
    """Cached compute_sample_files: a per-worker in-process memo (10 min) in
    front of a 6h SQLite row (study_detail_cache.sample_files_json, shared
    across workers and survives a restart). A study whose artifacts just
    finished processing can show stale availability for up to 6h."""
    sid = int(study_id)
    now = time.time()
    hit = _sample_files_memo.get(sid)
    if hit and now - hit[0] < _SAMPLE_FILES_MEMO_TTL_SECONDS:
        return hit[1]

    from store.cache import get_study_detail_cache, upsert_study_detail_cache
    cached = get_study_detail_cache(sid)
    if cached and cached.get("sample_files_json"):
        try:
            data = json.loads(cached["sample_files_json"])
            _sample_files_memo[sid] = (now, data)
            return data
        except (TypeError, ValueError):
            pass

    data = compute_sample_files(sid)
    upsert_study_detail_cache(sid, None, None, sample_files_json=json.dumps(data))
    _sample_files_memo[sid] = (now, data)
    return data
