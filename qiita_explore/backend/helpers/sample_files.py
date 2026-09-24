"""Per-sample sequence-file availability for the Sample Aggregation tab.

For every sample of one study: does it resolve to a per-sample sequence file
at all, and of which kind. Built from the same artifact groups as the export
(helpers/fastq_manifest._study_groups), so what the sample table shows is
exactly what the CSV can contain.

Cached two ways: a per-worker memo (cheap re-checks within a burst of page
requests) backed by the study_sample_files_cache table (6h TTL, shared across
workers and restarts). The table is the map's alone — it used to be a column
of study_detail_cache, where its partial writes poisoned the preps/artifacts
reads (TKT-086).
"""

import json
import time

from helpers.fastq_manifest import _study_groups, build_manifest_rows

_MEMO_TTL_SECONDS = 600
_memo = {}  # study_id -> (fetched_at_epoch, map); tests clear it


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
        rows, _paired = build_manifest_rows(samples, files, "")
        is_fasta = artifact_type == "FASTA"
        for sample_id, _fwd, rev in rows:
            cur = out.setdefault(sample_id, [0, 0])
            if is_fasta:
                cur[1] = 1
            else:
                cur[0] = max(cur[0], 2 if rev else 1)
    return out


def get_sample_files(study_id):
    """The study's availability map: memo → study_sample_files_cache → a live
    Postgres computation (then stored). A study whose artifacts finish
    processing shows up within the table's 6h TTL."""
    from store.cache import get_study_sample_files_cache, upsert_study_sample_files_cache

    sid = int(study_id)
    now = time.time()
    hit = _memo.get(sid)
    if hit and now - hit[0] < _MEMO_TTL_SECONDS:
        return hit[1]

    stored = get_study_sample_files_cache(sid)
    if stored:
        data = json.loads(stored)
    else:
        data = summarize_sample_files(_study_groups([sid], {}))
        upsert_study_sample_files_cache(sid, json.dumps(data))
    _memo[sid] = (now, data)
    return data
