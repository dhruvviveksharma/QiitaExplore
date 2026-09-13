"""Per-study sample listing for the Sample Aggregation tab: every sample id,
a paged + filterable slice with a few display columns, and the study's
metadata column list — all straight from qiita.sample_{study_id} (the
per-study JSONB table), so counts agree with fastq_manifest._SAMPLES_SQL and
never include the 'qiita_sample_column_names' sentinel row.

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


def _table(study_id):
    return f"qiita.sample_{int(study_id)}"


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
    now = time.time()
    hit = _columns_cache.get(sid)
    if hit and now - hit[0] < _COLUMNS_TTL_SECONDS:
        return hit[1]
    have = set(study_columns(sid))
    cols = [c for c in _PREFERRED if c in have][:_MAX_DISPLAY_COLUMNS]
    _columns_cache[sid] = (now, cols)
    return cols


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


def fetch_sample_page(study_id, offset, limit, q=None):
    """(rows, total, columns): rows = [(sample_id, value, ...)] in display-column
    order, total = number of samples matching q."""
    table = _table(study_id)
    columns = display_columns(study_id)
    where, wparams = _where(q)
    total = int(pooled_fetchall(f"SELECT COUNT(*) FROM {table} WHERE {where}", wparams)[0][0])
    select = ", ".join(["sample_id"] + ["sample_values->>%s"] * len(columns))
    # Binds follow the rendered SQL: select-list column names, then the WHERE
    # params, then LIMIT / OFFSET.
    rows = pooled_fetchall(
        f"SELECT {select} FROM {table} WHERE {where} ORDER BY sample_id LIMIT %s OFFSET %s",
        [*columns, *wparams, int(limit), int(offset)],
    )
    return rows, total, columns


def matching_sample_ids(study_id, q):
    where, params = _where(q)
    rows = pooled_fetchall(
        f"SELECT sample_id FROM {_table(study_id)} WHERE {where} ORDER BY sample_id", params,
    )
    return [r[0] for r in rows]
