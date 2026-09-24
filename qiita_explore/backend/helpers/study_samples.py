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

_PREP_DATA_TYPES_SQL = """
SELECT pts.sample_id, dt.data_type
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


def prep_data_types(study_id):
    """{sample_id: [data_type, ...]} from every prep the study has — the same
    membership (study_prep_template -> prep_template -> data_type ->
    prep_template_sample) the study card's data-type chips come from. Unlike
    helpers.sample_files, this knows about a sample regardless of whether it
    resolves to a per-sample sequence file. Memoized per worker for an hour
    (a study's prep set effectively never changes)."""
    sid = int(study_id)

    def compute():
        out = {}
        for sample_id, data_type in pooled_fetchall(_PREP_DATA_TYPES_SQL, [sid]):
            out.setdefault(sample_id, set()).add(data_type)
        return {k: sorted(v) for k, v in out.items()}

    return _memoized(_prep_dt_cache, _PREP_DT_TTL_SECONDS, sid, compute)


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
