"""Per-sample sequence-file paths for Qiita artifacts.

Qiita stores no sample↔file link; the mapping is a run_prefix substring match
against the filename, tried longest-prefix-first (mirrors
qiita_pet/handlers/api_proxy/studies.py). Absolute paths follow
qiita_db.util._path_builder: the artifact_id segment exists only when
data_directory.subdirectory is true (legacy raw_data files sit flat and were
renamed "{obj_id}_{basename}", which is why startswith() would miss them).

Two entry points share one JOIN block and one row builder so path logic can't
drift: fetch_manifest (a single per_sample_FASTQ artifact → QIIME2 V2 manifest,
used by the study modal) and fetch_export_rows (every per-sample sequence
artifact — per_sample_FASTQ fwd/rev and FASTA raw_fasta — across a Sample
Aggregation's studies, restricted to the checked samples; one row per sample ×
artifact with R1 / R2 / barcodes columns, shared by the CSV, TSV and xlsx).

_study_groups is the shared group-assembly step behind both fetch_export_rows
(checked samples → export rows) and helpers/sample_files (every sample of one
study → which have a file at all, and a page's file paths, for the sample
table).
"""

import csv
import io

from helpers.artifact_graph import _BASE
from helpers.pg_pool import pooled_fetchall

_FILES_FROM = """
SELECT sa.study_id, a.artifact_id, pa.prep_template_id, ft.filepath_type,
       dd.mountpoint, dd.subdirectory, f.filepath, dt.data_type, at.artifact_type,
       sc.name
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
LEFT JOIN qiita.software_command sc ON a.command_id = sc.command_id
"""
# The study-modal manifest is QIIME2-shaped and FASTQ-only.
_WHERE_FASTQ = """WHERE at.artifact_type = 'per_sample_FASTQ'
  AND ft.filepath_type IN ('raw_forward_seqs', 'raw_reverse_seqs')
"""
# The aggregate CSV / availability map also take Qiita's per-sample FASTA
# uploads (artifact type FASTA, one raw_fasta per sample). raw_qual is not
# sequence and is skipped. A raw multiplexed run (artifact type FASTQ) holds
# many samples per file, so it also brings its raw_barcodes file.
_WHERE_SEQ = """WHERE at.artifact_type IN ('per_sample_FASTQ', 'FASTA', 'FASTQ')
  AND (ft.filepath_type IN ('raw_forward_seqs', 'raw_reverse_seqs', 'raw_fasta')
       OR (at.artifact_type = 'FASTQ' AND ft.filepath_type = 'raw_barcodes'))
"""
_ARTIFACT_FILES_SQL = _FILES_FROM + _WHERE_FASTQ + "  AND sa.study_id = %s AND a.artifact_id = %s\n"
_STUDIES_FILES_SQL = _FILES_FROM + _WHERE_SEQ + "  AND sa.study_id = ANY(%s)\n"
_ARTIFACTS_CLAUSE = "  AND a.artifact_id = ANY(%s)\n"

_COUNT_SQL = """
SELECT COUNT(*)
FROM qiita.study_artifact sa
JOIN qiita.artifact a       ON sa.artifact_id = a.artifact_id
JOIN qiita.artifact_type at ON a.artifact_type_id = at.artifact_type_id
WHERE sa.study_id = %s AND at.artifact_type IN ('per_sample_FASTQ', 'FASTA', 'FASTQ')
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
EXPORT_HEADER = ["study_id", "sample_id", "artifact_id", "data_type", "processing", "R1", "R2", "barcodes"]
# An artifact's processing step is the Qiita command that produced it
# (e.g. "Atropos v1.1.24", "Adapter and host filtering v2023.12"); uploaded
# artifacts have no command. One metagenomic prep often holds several of these
# copies of the same reads, so the step is what tells them apart.
RAW_UPLOAD = "Raw upload"
XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _claim(pool, prefix):
    """Pop and return the path of the first pooled (filename, path) containing prefix."""
    for i, (filename, path) in enumerate(pool):
        if prefix in filename:
            del pool[i]
            return path
    return None


def _path(base_dir, mountpoint, subdirectory, artifact_id, filename):
    rel = f"{mountpoint}/{artifact_id}/{filename}" if subdirectory else f"{mountpoint}/{filename}"
    return f"{base_dir}/{rel}"


def build_manifest_rows(samples, files, base_dir):
    """samples: [(sample_id, run_prefix)]; files: [(filepath_type, mountpoint, subdirectory, artifact_id, filename)].

    Returns (rows, paired) with rows = [(sample_id, fwd_path, rev_path_or_None)]
    sorted by sample_id. Anything that isn't a reverse read (raw_forward_seqs,
    or a FASTA artifact's raw_fasta) is the sample's forward file. Samples with
    no run_prefix or no matching forward file are omitted.
    """
    fwd, rev = [], []
    for ftype, mountpoint, subdirectory, artifact_id, filename in files:
        (rev if ftype == "raw_reverse_seqs" else fwd).append(
            (filename, _path(base_dir, mountpoint, subdirectory, artifact_id, filename)))
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


def build_multiplexed_rows(samples, files, base_dir):
    """A raw multiplexed FASTQ artifact: one file set holds many samples, so
    nothing is claimed. Files of each type are sorted by name and zipped into
    lanes (fwd[i], rev[i], barcodes[i]); one lane serves every sample, several
    lanes go to the samples whose run_prefix is in that lane's forward name
    (the reverse name may not contain it: ..._R1_001 vs ..._R3_001).

    Returns [(sample_id, fwd, rev_or_None, barcodes_or_None)] sorted by
    sample_id; a sample with no lane is omitted."""
    by_type = {"raw_forward_seqs": [], "raw_reverse_seqs": [], "raw_barcodes": []}
    for ftype, mountpoint, subdirectory, artifact_id, filename in files:
        if ftype in by_type:
            by_type[ftype].append((filename, _path(base_dir, mountpoint, subdirectory, artifact_id, filename)))
    fwd, rev, bc = (sorted(by_type[t]) for t in ("raw_forward_seqs", "raw_reverse_seqs", "raw_barcodes"))
    lanes = [(f[0], f[1], rev[i][1] if i < len(rev) else None, bc[i][1] if i < len(bc) else None)
             for i, f in enumerate(fwd)]
    rows = []
    for sample_id, prefix in samples:
        lane = lanes[0] if len(lanes) == 1 else next((ln for ln in lanes if prefix and prefix in ln[0]), None)
        if lane:
            rows.append((sample_id, lane[1], lane[2], lane[3]))
    rows.sort()
    return rows


def _resolve(artifact_type, samples, files, base_dir):
    """The one dispatch for every consumer: [(sample_id, fwd, rev, barcodes)]
    whether the artifact is per-sample (barcodes None) or a pooled FASTQ run."""
    if artifact_type == "FASTQ":
        return build_multiplexed_rows(samples, files, base_dir)
    rows, _ = build_manifest_rows(samples, files, base_dir)
    return [(sid, fwd, rev, None) for sid, fwd, rev in rows]


def group_matches(file_filter, data_type, processing, artifact_id=None):
    """file_filter: {"data_types": [...], "processing": [...], "artifacts":
    ["140751", ...]} or None; an empty (or missing) list means any. A group is one whole artifact, and
    run_prefix claiming is per artifact, so dropping non-matching groups never
    changes which file a kept group's sample resolves to."""
    f = file_filter or {}
    dts, procs, arts = f.get("data_types") or [], f.get("processing") or [], f.get("artifacts") or []
    return ((not dts or data_type in dts) and (not procs or processing in procs)
            and (not arts or str(artifact_id) in arts))


def build_export_rows(groups, base_dir):
    """groups: [(study_id, data_type, artifact_type, processing, artifact_id,
    samples, files, allow)], one per artifact. `samples` is the prep's FULL
    (sample_id, run_prefix) list — longest-prefix-first claiming needs every
    sample present, or a selected '8B4' would take '8B4ABX_R1.fastq.gz' once
    unselected '8B4ABX' were filtered away. `allow` is the set of checked
    sample ids; only their rows are emitted.

    Returns sorted, de-duplicated (study_id, sample_id, artifact_id, data_type,
    processing, R1, R2, barcodes) rows, blanks as '' — one per sample × artifact.
    R1 is the forward read (or a FASTA artifact's raw_fasta). A sample in two
    preps or two processing copies yields a row for each; pooled runs repeat
    the same paths for every sample of the prep.
    """
    out = set()
    for study_id, data_type, artifact_type, processing, artifact_id, samples, files, allow in groups:
        for sample_id, fwd, rev, bc in _resolve(artifact_type, samples, files, base_dir):
            if sample_id in allow:
                out.add((study_id, sample_id, artifact_id, data_type, processing, fwd, rev or "", bc or ""))
    return sorted(out)


def to_tsv(rows, paired):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    w.writerow(_PAIRED_HEADER if paired else _SINGLE_HEADER)
    for sample_id, fwd, rev in rows:
        w.writerow([sample_id, fwd, rev or ""] if paired else [sample_id, fwd])
    return buf.getvalue()


def to_csv(rows, delimiter=","):
    """The export for scripts / pandas (delimiter="\t" gives the TSV). Opened in
    Excel or Numbers, an id like 10317.000001062 parses as a number and shows as
    10317 — use to_xlsx for spreadsheets."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    w.writerow(EXPORT_HEADER)
    w.writerows(rows)
    return buf.getvalue()


def to_xlsx(rows):
    """The same rows as an Excel workbook. Every column but study_id is a text
    cell with number format '@', so Excel / Numbers keep 10317.000001062 as
    typed instead of parsing it as a number (which is what made the CSV's
    sample_id look like the study_id)."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("samples")
    ws.freeze_panes = "A2"
    for col, width in zip("ABCDEFGH", (10, 22, 12, 18, 36, 90, 90, 90)):
        ws.column_dimensions[col].width = width
    bold = Font(bold=True)

    def cell(value, text=True, font=None):
        c = WriteOnlyCell(ws, value=value)
        if text:
            c.number_format = "@"
        if font:
            c.font = font
        return c

    ws.append([cell(h, font=bold) for h in EXPORT_HEADER])
    for study_id, *rest in rows:
        ws.append([cell(int(study_id), text=False)] + [cell(str(v)) for v in rest])
    buf = io.BytesIO()
    wb.save(buf)
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


def _study_groups(study_ids, selected, artifact_ids=None):
    """Every sequence artifact (per_sample_FASTQ, FASTA, pooled FASTQ) of
    study_ids — or only artifact_ids, when given — bucketed into (study_id,
    data_type, artifact_type, processing, artifact_id, samples, files, allow)
    groups — one per artifact, `samples` the prep's FULL (sample_id,
    run_prefix) list, `allow` = selected.get(study_id, set()).
    [] when study_ids is empty or none of them has such an artifact."""
    if not study_ids:
        return []
    if artifact_ids is None:
        frows = pooled_fetchall(_STUDIES_FILES_SQL, [study_ids])
    else:
        frows = pooled_fetchall(_STUDIES_FILES_SQL + _ARTIFACTS_CLAUSE, [study_ids, list(artifact_ids)])
    if not frows:
        return []
    # Bucket by (study_id, prep_id, artifact_id); iterate sorted for determinism.
    by_artifact = {}
    for r in frows:
        by_artifact.setdefault((r[0], r[2], r[1]), []).append(r)
    samples_by_prep = {}  # a prep can own several artifacts; fetch its samples once
    groups = []
    for key in sorted(by_artifact):
        study_id, pid, artifact_id = key
        if pid not in samples_by_prep:
            samples_by_prep[pid] = pooled_fetchall(_SAMPLES_SQL.format(pid=int(pid)))
        first = by_artifact[key][0]
        groups.append((study_id, first[7], first[8], first[9] or RAW_UPLOAD, artifact_id, samples_by_prep[pid],
                       _files(by_artifact[key]), selected.get(study_id, set())))
    return groups


def _export_groups(selected, file_filters):
    """file_filters: {study_id: file_filter} — each study's groups are matched
    against that study's own filter (a missing study means no filter)."""
    if not _BASE:
        raise RuntimeError("QIITA_BASE_DATA_DIR is not set; export paths would be relative")
    study_ids = sorted(int(s) for s, ids in selected.items() if ids)
    filters = {int(k): v for k, v in (file_filters or {}).items()}
    groups = [g for g in _study_groups(study_ids, selected)
              if group_matches(filters.get(int(g[0])), g[1], g[3], g[4])]
    if not groups:
        raise ValueError("No sequence artifacts of the chosen data type / processing "
                         "in the selected studies")
    return groups


def fetch_export_rows(selected, file_filters=None):
    """selected: {study_id: {sample_id, ...}} — empty sets are ignored.
    file_filters: {study_id: that study's saved {"data_types", "processing",
    "artifacts"}} (see group_matches). Returns build_export_rows over every
    matching sequence artifact of those studies. Raises ValueError when no such
    artifact exists or no checked sample resolves to a file."""
    rows = build_export_rows(_export_groups(selected, file_filters), _BASE)
    if not rows:
        raise ValueError("None of the selected samples has a sequence file")
    return rows
