"""Per-sample sequence-file availability for the Sample Aggregation tab.

For every sample of one study: which per-sample sequence files it resolves
to, by data type and processing step. Built from the same artifact groups as
the export (helpers/fastq_manifest._study_groups) and filtered with the same
group_matches, so what the sample table shows is exactly what the CSV / xlsx
can contain.

The map is {sample_id: [(data_type, processing, fastq, fasta), ...]} — one
entry per data type × processing step the sample has a file in; fastq is 2
(paired), 1 (single) or 0, fasta 1 or 0, each the max across that pair's
artifacts. Only samples with at least one file appear.

Cached two ways: a per-worker memo (cheap re-checks within a burst of page
requests) backed by the study_sample_files_cache table (6h TTL, shared across
workers and restarts). The table is the map's alone — it used to be a column
of study_detail_cache, where its partial writes poisoned the preps/artifacts
reads (TKT-086).
"""

import json
import time
from collections import Counter

from helpers.fastq_manifest import _study_groups, build_manifest_rows, group_matches

_MEMO_TTL_SECONDS = 600
_memo = {}  # study_id -> (fetched_at_epoch, map); tests clear it
# Stored-JSON format version. A row in any other shape (or none) is recomputed.
_FORMAT = 2


def summarize_sample_files(groups):
    """groups: _study_groups output — `allow` is ignored (availability covers
    every sample of the study, not just checked ones), and paths don't matter,
    so build_manifest_rows runs with an empty base_dir. Returns the map
    described in the module docstring, entries sorted."""
    acc = {}  # sample_id -> {(data_type, processing): [fastq, fasta]}
    for _study_id, data_type, artifact_type, processing, samples, files, _allow in groups:
        rows, _paired = build_manifest_rows(samples, files, "")
        is_fasta = artifact_type == "FASTA"
        for sample_id, _fwd, rev in rows:
            cur = acc.setdefault(sample_id, {}).setdefault((data_type, processing), [0, 0])
            if is_fasta:
                cur[1] = 1
            else:
                cur[0] = max(cur[0], 2 if rev else 1)
    return {sid: sorted((dt, proc, fq, fa) for (dt, proc), (fq, fa) in pairs.items())
            for sid, pairs in acc.items()}


def encode(files):
    """Compact JSON: the label strings are interned (AGP repeats the same few
    data types / steps across ~7k samples)."""
    dts = sorted({e[0] for es in files.values() for e in es})
    procs = sorted({e[1] for es in files.values() for e in es})
    di, pi = {d: i for i, d in enumerate(dts)}, {p: i for i, p in enumerate(procs)}
    return json.dumps({
        "v": _FORMAT, "dt": dts, "proc": procs,
        "s": {sid: [[di[dt], pi[proc], fq, fa] for dt, proc, fq, fa in es] for sid, es in files.items()},
    }, separators=(",", ":"))


def decode(text):
    """encode()'s inverse; None for anything not in the current format."""
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("v") != _FORMAT:
        return None
    dts, procs = d["dt"], d["proc"]
    return {sid: [(dts[i], procs[j], fq, fa) for i, j, fq, fa in es] for sid, es in d["s"].items()}


def get_sample_files(study_id):
    """The study's map: memo → study_sample_files_cache → a live Postgres
    computation (then stored). A study whose artifacts finish processing
    shows up within the table's 6h TTL."""
    from store.cache import get_study_sample_files_cache, upsert_study_sample_files_cache

    sid = int(study_id)
    now = time.time()
    hit = _memo.get(sid)
    if hit and now - hit[0] < _MEMO_TTL_SECONDS:
        return hit[1]

    stored = get_study_sample_files_cache(sid)
    data = decode(stored) if stored else None
    if data is None:
        data = summarize_sample_files(_study_groups([sid], {}))
        upsert_study_sample_files_cache(sid, encode(data))
    _memo[sid] = (now, data)
    return data


def effective(files, file_filter):
    """{sample_id: (fastq, fasta)} over only the entries the aggregation's
    filter keeps; samples with no matching file are absent."""
    out = {}
    for sid, entries in files.items():
        kept = [(fq, fa) for dt, proc, fq, fa in entries if group_matches(file_filter, dt, proc)]
        if kept:
            out[sid] = (max(k[0] for k in kept), max(k[1] for k in kept))
    return out


def in_scope(prep_types, entries, file_filter):
    """Whether a sample belongs in the sample table under the saved
    file_filter's data-type choice. True when no data type is chosen, or when
    the sample's prep membership (helpers.study_samples.prep_data_types) or
    any of its file entries' data types intersect the chosen set. Processing
    never narrows scope — it describes files, not preps, so a sample with no
    per-sample file (e.g. a 16S study with only Demultiplexed/BIOM artifacts)
    can still be in scope and shown with FASTQ/FASTA "—"."""
    want_dts = (file_filter or {}).get("data_types") or []
    if not want_dts:
        return True
    types = set(prep_types or [])
    types.update(dt for dt, _proc, _fq, _fa in (entries or []))
    return bool(types & set(want_dts))


def facet_counts(file_maps, prep_maps, file_filter):
    """Picker options across several studies' data. file_maps / prep_maps are
    each an iterable of one dict per study (get_sample_files /
    helpers.study_samples.prep_data_types); sample ids are globally unique
    (study-prefixed), so the two need not align position-for-position.

    Data-type counts are distinct samples IN SCOPE for that type (in_scope,
    ignoring processing — a picked processing step never hides a data type).
    Processing counts are file-based, honoring the data-type filter (each
    facet ignores its own selection, so picking one option doesn't hide the
    others). Selected names with nothing left are kept at count 0, so they
    can still be unticked."""
    f = file_filter or {}
    want_dts = f.get("data_types") or []
    want_procs = f.get("processing") or []

    files_by_sample = {}
    for files in file_maps:
        files_by_sample.update(files)
    prep_by_sample = {}
    for preps in prep_maps:
        prep_by_sample.update(preps)

    dt_counts, proc_counts = Counter(), Counter()
    for sid in set(files_by_sample) | set(prep_by_sample):
        entries = files_by_sample.get(sid, [])
        dt_counts.update(set(prep_by_sample.get(sid, ())) | {dt for dt, _proc, _fq, _fa in entries})
        proc_counts.update({proc for dt, proc, _fq, _fa in entries if not want_dts or dt in want_dts})

    def options(counts, selected):
        names = sorted(set(counts) | set(selected))
        return [{"name": n, "count": counts.get(n, 0)} for n in names]

    return options(dt_counts, want_dts), options(proc_counts, want_procs)
