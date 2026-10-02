"""Per-study sample listing for the Sample Aggregation tab: every sample id,
the study's metadata column list, and a lookup of one page of samples by id —
all straight from qiita.sample_{study_id} (the per-study JSONB table), so
counts agree with fastq_manifest._SAMPLES_SQL and never include the
'qiita_sample_column_names' sentinel row.

Paging happens in Python (routes/aggregation_routes.py), not SQL: the sample
table sorts files-first using helpers.sample_files.get_sample_files, which
plain SQL LIMIT/OFFSET cannot express. fetch_samples_by_ids fetches exactly
one page's metadata, by id, in whatever order the caller already decided.

The table name is f-string interpolated from int(study_id) — the same
trusted-int pattern as routes/artifact_routes.py; every value is bound.
"""

import time

from helpers.pg_pool import pooled_fetchall

SENTINEL = "qiita_sample_column_names"

# Display columns for the sample table, in preference order; the first four a
# study actually has are shown. Everything else is one click away in the
# metadata pane.
_PREFERRED = [
    "sample_type", "host_body_site", "body_site", "env_material", "empo_3",
    "host_scientific_name", "scientific_name", "host_common_name",
    "collection_timestamp", "description",
]
_MAX_DISPLAY_COLUMNS = 4
_COLUMNS_TTL_SECONDS = 3600
_columns_cache = {}  # study_id -> (fetched_at_epoch, [column, ...]); tests clear it

_PREP_DT_TTL_SECONDS = 3600
_prep_dt_cache = {}  # study_id -> (fetched_at_epoch, {sample_id: [data_type, ...]}); tests clear it

_artifact_prep_cache = {}  # study_id -> (fetched_at_epoch, {artifact_id: prep_id}); tests clear it

# Qiita keeps artifact -> prep in preparation_artifact; every artifact of a study
# belongs to exactly one prep (checked on AGP: 5,969 of 5,969).
_ARTIFACT_PREPS_SQL = """
SELECT pa.artifact_id, pa.prep_template_id
FROM qiita.study_artifact sa
JOIN qiita.preparation_artifact pa ON pa.artifact_id = sa.artifact_id
WHERE sa.study_id = %s
ORDER BY pa.prep_template_id
"""

_PREP_DATA_TYPES_SQL = """
SELECT pts.sample_id, pts.prep_template_id, dt.data_type
FROM qiita.study_prep_template spt
JOIN qiita.prep_template pt ON pt.prep_template_id = spt.prep_template_id
JOIN qiita.data_type dt ON pt.data_type_id = dt.data_type_id
JOIN qiita.prep_template_sample pts ON pts.prep_template_id = pt.prep_template_id
WHERE spt.study_id = %s
"""


def _table(study_id):
    return f"qiita.sample_{int(study_id)}"


def _memoized(cache, ttl_seconds, key, compute):
    """Shared per-worker TTL-memo idiom: cache[key] = (fetched_at, value).
    Tests clear the module-level dict directly by name."""
    now = time.time()
    hit = cache.get(key)
    if hit and now - hit[0] < ttl_seconds:
        return hit[1]
    value = compute()
    cache[key] = (now, value)
    return value


def list_study_sample_ids(study_id):
    rows = pooled_fetchall(
        f"SELECT sample_id FROM {_table(study_id)} WHERE sample_id <> %s ORDER BY sample_id",
        [SENTINEL],
    )
    return [r[0] for r in rows]


def study_columns(study_id):
    """Every metadata column of the study, read from the sentinel row Qiita
    keeps in each sample table (sample_values = {"columns": [...]}); [] when
    the sentinel is missing."""
    rows = pooled_fetchall(
        f"SELECT sample_values->'columns' FROM {_table(study_id)} WHERE sample_id = %s",
        [SENTINEL],
    )
    cols = rows[0][0] if rows else None
    return [c for c in cols if isinstance(c, str)] if isinstance(cols, list) else []


def display_columns(study_id):
    """First _MAX_DISPLAY_COLUMNS of _PREFERRED the study has; memoized per
    worker for an hour (the column set of a study effectively never changes)."""
    sid = int(study_id)

    def compute():
        have = set(study_columns(sid))
        return [c for c in _PREFERRED if c in have][:_MAX_DISPLAY_COLUMNS]

    return _memoized(_columns_cache, _COLUMNS_TTL_SECONDS, sid, compute)


def prep_membership(study_id):
    """{sample_id: [(prep_id, data_type), ...]} sorted by prep id, from every
    prep the study has (study_prep_template -> prep_template -> data_type ->
    prep_template_sample) — the same membership the study card's data-type
    chips come from. Unlike helpers.sample_files, this knows about a sample
    regardless of whether it resolves to a per-sample sequence file. Memoized
    per worker for an hour (a study's prep set effectively never changes)."""
    sid = int(study_id)

    def compute():
        out = {}
        for sample_id, prep_id, data_type in pooled_fetchall(_PREP_DATA_TYPES_SQL, [sid]):
            out.setdefault(sample_id, set()).add((prep_id, data_type))
        return {k: sorted(v) for k, v in out.items()}

    return _memoized(_prep_dt_cache, _PREP_DT_TTL_SECONDS, sid, compute)


def artifact_preps(study_id):
    """{artifact_id: prep_id} for every artifact of the study — what ties a file
    row to its prep. Memoized per worker for an hour, like prep_membership."""
    sid = int(study_id)

    def compute():
        out = {}
        for artifact_id, prep_id in pooled_fetchall(_ARTIFACT_PREPS_SQL, [sid]):
            out.setdefault(artifact_id, prep_id)   # ORDER BY prep id: the lowest wins, deterministically
        return out

    return _memoized(_artifact_prep_cache, _PREP_DT_TTL_SECONDS, sid, compute)


def prep_data_types(study_id):
    """{sample_id: [data_type, ...]} — prep_membership without the prep ids."""
    return {sid: sorted({dt for _pid, dt in preps}) for sid, preps in prep_membership(study_id).items()}


def prep_groups(study_id):
    """[{"prep_id", "data_type", "num_samples"}] ascending by prep id, then a
    {"prep_id": None, "data_type": None, "num_samples": n} entry for the
    study's samples that are in no prep (omitted when there are none)."""
    membership = prep_membership(study_id)
    counts, types = {}, {}
    for preps in membership.values():
        for pid, dt in preps:
            counts[pid] = counts.get(pid, 0) + 1
            types[pid] = dt
    groups = [{"prep_id": pid, "data_type": types[pid], "num_samples": counts[pid]} for pid in sorted(counts)]
    orphans = sum(1 for sid in list_study_sample_ids(study_id) if sid not in membership)
    if orphans:
        groups.append({"prep_id": None, "data_type": None, "num_samples": orphans})
    return groups


def fetch_prep_samples(study_id, prep_id, limit):
    """(samples, total) for one prep: [{sample_id, anonymized_name,
    collection_timestamp, env_package, prep_ids}] by sample id, the first
    `limit` of `total`. prep_id None = the study's samples in no prep."""
    membership = prep_membership(study_id)
    if prep_id is None:
        ids = [sid for sid in list_study_sample_ids(study_id) if sid not in membership]
    else:
        ids = sorted(sid for sid, preps in membership.items() if any(p == prep_id for p, _dt in preps))
    page = ids[:limit]
    rows = pooled_fetchall(
        f"SELECT sample_id, sample_values->>'anonymized_name', sample_values->>'collection_timestamp', "
        f"sample_values->>'env_package' FROM {_table(study_id)} WHERE sample_id = ANY(%s) ORDER BY sample_id",
        [page],
    ) if page else []
    return [{"sample_id": r[0], "anonymized_name": r[1], "collection_timestamp": r[2], "env_package": r[3],
             "prep_ids": [p for p, _dt in membership.get(r[0], [])]} for r in rows], len(ids)


def _where(q):
    """(sql, params): sentinel excluded, plus an optional case-insensitive
    substring match on the sample id or any metadata value (sample_values::text,
    as helpers/sample_search does). '%' and '_' in q act as wildcards."""
    sql, params = "sample_id <> %s", [SENTINEL]
    q = (q or "").strip()
    if q:
        sql += " AND (sample_id ILIKE %s OR sample_values::text ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    return sql, params


def matching_sample_ids(study_id, q):
    where, params = _where(q)
    rows = pooled_fetchall(
        f"SELECT sample_id FROM {_table(study_id)} WHERE {where} ORDER BY sample_id", params,
    )
    return [r[0] for r in rows]


def fetch_samples_by_ids(study_id, sample_ids):
    """rows = [(sample_id, value, ...)] in display-column order, for exactly
    the given sample_ids, re-ordered to match the input order — availability-
    first paging computes order in Python, so SQL's own row order is
    irrelevant. [] (no query at all) when sample_ids is empty."""
    ids = list(sample_ids)
    if not ids:
        return []
    columns = display_columns(study_id)
    select = ", ".join(["sample_id"] + ["sample_values->>%s"] * len(columns))
    rows = pooled_fetchall(
        f"SELECT {select} FROM {_table(study_id)} WHERE sample_id = ANY(%s)",
        [*columns, ids],
    )
    order = {sid: i for i, sid in enumerate(ids)}
    rows.sort(key=lambda r: order.get(r[0], len(ids)))
    return rows


def fetch_sample_fields(study_id, sample_id):
    """One sample's metadata as {field: value} (without the internal
    qiita_study_id key), or None when the study has no such sample."""
    rows = pooled_fetchall(
        f"SELECT sample_values FROM {_table(study_id)} WHERE sample_id = %s AND sample_id <> %s",
        [sample_id, SENTINEL],
    )
    if not rows:
        return None
    fields = dict(rows[0][0])
    fields.pop("qiita_study_id", None)
    return fields
