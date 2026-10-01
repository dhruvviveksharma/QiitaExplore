"""Per-sample sequence-file availability for the Sample Aggregation tab.

For every sample of one study: which sequence files (per-sample or pooled run) it resolves
to, by data type and processing step. Built from the same artifact groups as
the export (helpers/fastq_manifest._study_groups) and filtered with the same
group_matches, so what the sample table shows is exactly what the CSV / xlsx
can contain.

The map is {sample_id: [(data_type, processing, artifact_id, fastq, fasta),
...]} — one entry per artifact the sample has a file in; fastq is 2 (paired),
1 (single) or 0, fasta 1 or 0. Only samples with at least one file appear. File
paths are not cached (AGP's would be ~15 MB): page_files resolves them for one
page of samples at a time.

Cached two ways: a per-worker memo (cheap re-checks within a burst of page
requests) backed by the study_sample_files_cache table (6h TTL, shared across
workers and restarts). The table is the map's alone — it used to be a column
of study_detail_cache, where its partial writes poisoned the preps/artifacts
reads (TKT-086).
"""

import json
import time
from collections import Counter

from helpers import fastq_manifest as _fm
from helpers.fastq_manifest import _resolve, _study_groups, group_matches

_MEMO_TTL_SECONDS = 600
_memo = {}  # study_id -> (fetched_at_epoch, map); tests clear it
# Stored-JSON format version. A row in any other shape (or none) is recomputed.
_FORMAT = 4


def summarize_sample_files(groups):
    """groups: _study_groups output — `allow` is ignored (availability covers
    every sample of the study, not just checked ones), and paths don't matter,
    so _resolve runs with an empty base_dir. Returns the map
    described in the module docstring, entries sorted."""
    acc = {}  # sample_id -> [(data_type, processing, artifact_id, fastq, fasta)]
    for _study_id, data_type, artifact_type, processing, artifact_id, samples, files, _allow in groups:
        is_fasta = artifact_type == "FASTA"
        for sample_id, _fwd, rev, _bc in _resolve(artifact_type, samples, files, ""):
            fastq = 0 if is_fasta else (2 if rev else 1)
            acc.setdefault(sample_id, []).append((data_type, processing, artifact_id, fastq, int(is_fasta)))
    return {sid: sorted(entries) for sid, entries in acc.items()}


def encode(files):
    """Compact JSON: the label strings are interned (AGP repeats the same few
    data types / steps across ~7k samples)."""
    dts = sorted({e[0] for es in files.values() for e in es})
    procs = sorted({e[1] for es in files.values() for e in es})
    di, pi = {d: i for i, d in enumerate(dts)}, {p: i for i, p in enumerate(procs)}
    return json.dumps({
        "v": _FORMAT, "dt": dts, "proc": procs,
        "s": {sid: [[di[dt], pi[proc], aid, fq, fa] for dt, proc, aid, fq, fa in es] for sid, es in files.items()},
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
    return {sid: [(dts[i], procs[j], aid, fq, fa) for i, j, aid, fq, fa in es] for sid, es in d["s"].items()}


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
        kept = [(fq, fa) for dt, proc, aid, fq, fa in entries if group_matches(file_filter, dt, proc, aid)]
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
    can still be in scope and shown with FASTQ/FASTA "—".

    A chosen artifact id is stricter: the sample must have a file in one of
    the chosen artifacts (of a chosen data type, if any)."""
    f = file_filter or {}
    want_dts, want_arts = f.get("data_types") or [], f.get("artifacts") or []
    if want_arts:
        return any(str(aid) in want_arts and (not want_dts or dt in want_dts)
                   for dt, _proc, aid, _fq, _fa in (entries or []))
    if not want_dts:
        return True
    types = set(prep_types or [])
    types.update(e[0] for e in (entries or []))
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
    others). Artifact counts are distinct samples with a file in that artifact
    under the data-type + processing picks. Selected names with nothing left
    are kept at count 0, so they can still be unticked. Returns (data_types,
    processing, artifacts)."""
    f = file_filter or {}
    want_dts = f.get("data_types") or []
    want_procs = f.get("processing") or []
    want_arts = f.get("artifacts") or []
    dt_proc = {"data_types": want_dts, "processing": want_procs}

    files_by_sample = {}
    for files in file_maps:
        files_by_sample.update(files)
    prep_by_sample = {}
    for preps in prep_maps:
        prep_by_sample.update(preps)

    dt_counts, proc_counts, art_counts = Counter(), Counter(), Counter()
    for sid in set(files_by_sample) | set(prep_by_sample):
        entries = files_by_sample.get(sid, [])
        dt_counts.update(set(prep_by_sample.get(sid, ())) | {e[0] for e in entries})
        proc_counts.update({proc for dt, proc, *_ in entries if not want_dts or dt in want_dts})
        art_counts.update({str(aid) for dt, proc, aid, *_ in entries if group_matches(dt_proc, dt, proc)})

    def options(counts, selected, key=None):
        names = sorted(set(counts) | set(selected), key=key)
        return [{"name": n, "count": counts.get(n, 0)} for n in names]

    # Artifact ids are digit strings: (len, str) orders them numerically.
    return (options(dt_counts, want_dts), options(proc_counts, want_procs),
            options(art_counts, want_arts, key=lambda n: (len(n), n)))


def page_files(study_id, sample_ids, all_files, file_filter):
    """{sample_id: [{artifact_id, data_type, processing, r1, r2, barcodes}, ...]}
    for one page of samples, ordered by artifact id: the paths of each sample's
    files under the filter (blanks as ''). all_files is get_sample_files'
    map; it names the page's artifacts, so only those are queried. Resolved
    against each artifact's full prep, same as the export."""
    out = {sid: [] for sid in sample_ids}
    aids = sorted({e[2] for sid in sample_ids for e in all_files.get(sid, [])
                   if group_matches(file_filter, e[0], e[1], e[2])})
    if not aids:
        return out
    for _study, dt, atype, proc, aid, samples, files, _allow in _study_groups([int(study_id)], {}, aids):
        for sid, fwd, rev, bc in _resolve(atype, samples, files, _fm._BASE):
            if sid in out:
                out[sid].append({"artifact_id": aid, "data_type": dt, "processing": proc,
                                 "r1": fwd, "r2": rev or "", "barcodes": bc or ""})
    for entries in out.values():
        entries.sort(key=lambda e: e["artifact_id"])
    return out
