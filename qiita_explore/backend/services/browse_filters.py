"""Browse-page facet filters (PI, data type, year added): request parsing,
the SQL fragment ANDed into the browse WHERE, the Python mirror for rows that
bypass that WHERE (deep-search sample-metadata hits are hydrated by id), and
the cached facet lists the UI's pickers are fed from.

Year = EXTRACT(YEAR FROM qiita.study.first_contact) — when the study was
created in Qiita. Qiita stores no publication date (study_publication holds
DOI / PubMed IDs only), so the UI labels this "Year added".
"""
import time

from helpers.pg_pool import pooled_fetchall
from helpers.qiita_fetch import _PUBLIC_ARTIFACT_EXISTS

_MAX_PIS = 50
_MAX_DATA_TYPES = 20
_YEAR_SQL = "EXTRACT(YEAR FROM s.first_contact)"


def _opt_int(value, key):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer") from None


def _str_list(values, cap):
    if not isinstance(values, list):
        return []
    out = []
    for v in values:
        if isinstance(v, str) and v.strip() and v.strip() not in out:
            out.append(v.strip())
    return out[:cap]


def parse_browse_filters(raw):
    """Normalise the request's `filters` object; None when nothing is set.
    Raises ValueError on a non-integer year (the route turns that into 400)."""
    if not isinstance(raw, dict):
        return None
    f = {
        "pis":        _str_list(raw.get("pis"), _MAX_PIS),
        "data_types": _str_list(raw.get("data_types"), _MAX_DATA_TYPES),
        "year_min":   _opt_int(raw.get("year_min"), "year_min"),
        "year_max":   _opt_int(raw.get("year_max"), "year_max"),
    }
    if f["year_min"] is not None and f["year_max"] is not None and f["year_min"] > f["year_max"]:
        f["year_min"], f["year_max"] = f["year_max"], f["year_min"]
    if not (f["pis"] or f["data_types"] or f["year_min"] is not None or f["year_max"] is not None):
        return None
    return f


def build_browse_filter_where(pis=None, year_min=None, year_max=None):
    """(sql, params) AND-fragment over search_studies_with_sql's `s` / `sp_pi`
    aliases, or ("", []). The caller ANDs it into custom_sql_where alongside
    that clause's own params, so it binds in the topic slot and the builder's
    load-bearing param order gains no new slot. Data types are not here —
    they use the existing `data_types=` kwarg (build_data_type_filter)."""
    parts, params = [], []
    if pis:
        parts.append("sp_pi.name = ANY(%s)")
        params.append(list(pis))
    if year_min is not None:
        parts.append(f"{_YEAR_SQL} >= %s")
        params.append(int(year_min))
    if year_max is not None:
        parts.append(f"{_YEAR_SQL} <= %s")
        params.append(int(year_max))
    return " AND ".join(parts), params


def study_passes_browse_filters(study, pis=None, data_types=None, year_min=None, year_max=None):
    """Python mirror of build_browse_filter_where + the data-type filter, for
    merged result rows (deep-search hits never saw the browse WHERE)."""
    if pis and (study.get("pi_name") or "") not in pis:
        return False
    if data_types:
        have = {t.strip() for t in (study.get("data_types") or "").split(",") if t.strip()}
        if not have & set(data_types):
            return False
    year = study.get("year")
    if year_min is not None and (year is None or year < year_min):
        return False
    if year_max is not None and (year is None or year > year_max):
        return False
    return True


# ── Facets ───────────────────────────────────────────────────────────────────
_FACETS_TTL_SECONDS = 6 * 3600
_facets_cache = {"at": 0.0, "data": None}  # per-worker, like qiita_fetch._study_header_cache

# Grouped by sp_pi.name because that is what build_browse_filter_where matches on.
_PI_FACET_SQL = f"""
    SELECT sp_pi.name, COUNT(*) AS n
    FROM qiita.study s
    JOIN qiita.study_person sp_pi ON s.principal_investigator_id = sp_pi.study_person_id
    WHERE {_PUBLIC_ARTIFACT_EXISTS} AND COALESCE(sp_pi.name, '') <> ''
    GROUP BY sp_pi.name
    ORDER BY n DESC, sp_pi.name
"""
_YEAR_FACET_SQL = f"""
    SELECT MIN({_YEAR_SQL})::int, MAX({_YEAR_SQL})::int
    FROM qiita.study s
    WHERE {_PUBLIC_ARTIFACT_EXISTS}
"""
_DATA_TYPE_FACET_SQL = f"""
    SELECT dt.data_type, COUNT(DISTINCT s.study_id) AS n
    FROM qiita.study s
    JOIN qiita.study_prep_template spt ON spt.study_id = s.study_id
    JOIN qiita.prep_template pt ON spt.prep_template_id = pt.prep_template_id
    JOIN qiita.data_type dt ON pt.data_type_id = dt.data_type_id
    WHERE {_PUBLIC_ARTIFACT_EXISTS}
    GROUP BY dt.data_type
    ORDER BY n DESC, dt.data_type
"""


def get_search_facets():
    """{"pis": [{name, count}], "years": {min, max}, "data_types": [{name, count}]}
    over public studies, recomputed at most every 6 h per worker."""
    now = time.time()
    if _facets_cache["data"] is not None and now - _facets_cache["at"] < _FACETS_TTL_SECONDS:
        return _facets_cache["data"]
    pis   = pooled_fetchall(_PI_FACET_SQL)
    years = pooled_fetchall(_YEAR_FACET_SQL)
    dts   = pooled_fetchall(_DATA_TYPE_FACET_SQL)
    lo, hi = years[0] if years else (None, None)
    data = {
        "pis":        [{"name": r[0], "count": r[1]} for r in pis],
        "years":      {"min": lo, "max": hi},
        "data_types": [{"name": r[0], "count": r[1]} for r in dts],
    }
    _facets_cache.update(at=now, data=data)
    return data
