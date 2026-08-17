# backend/services/study_service.py
import logging

from helpers.pg_pool import pooled_fetchall
from helpers.qiita_fetch import _STUDY_COUNT_COLUMNS, _row_to_study_header
from services.relevance import RELEVANCE_WEIGHTS

logger = logging.getLogger(__name__)

# ── Morphological variants ────────────────────────────────────────────────────
_IRREGULAR_VARIANTS = {
    "mouse": "mice",     "mice": "mouse",
    "louse": "lice",     "lice": "louse",
    "bacterium": "bacteria", "bacteria": "bacterium",
    "fungus": "fungi",   "fungi": "fungus",
    "alga": "algae",     "algae": "alga",
    "virus": "viruses",  "viruses": "virus",
}


def expand_keyword_variants(keywords):
    """Add plural/singular variants (including mouse↔mice). Caps at 80."""
    seen, expanded = set(), []
    for kw in (keywords or []):
        kw = kw.strip()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        expanded.append(kw)
        kl = kw.lower()
        if kl in _IRREGULAR_VARIANTS:
            alt = _IRREGULAR_VARIANTS[kl]
            if alt not in seen:
                seen.add(alt)
                expanded.append(alt)
        elif not kl.endswith("s") and len(kl) > 2:
            plural = kw + "s"
            if plural not in seen:
                seen.add(plural)
                expanded.append(plural)
    return expanded[:80]


# ── Data-type synonym map (based on the 12 real Qiita data_type values) ───────
DATA_TYPE_SYNONYMS = {
    "Metagenomic": [
        "shotgun", "wgs", "whole genome shotgun", "whole metagenome",
        "metagenome", "metagenomic", "metagenomics", "functional profiling",
        "shotgun sequencing", "shotgun metagenomics",
    ],
    "16S": [
        "16s", "amplicon", "rrna", "16s rrna", "16s rdna",
        "target gene", "v3v4", "v4 region", "16s sequencing",
    ],
    "18S": ["18s", "18s rrna", "eukaryotic amplicon"],
    "ITS": ["its", "its1", "its2", "fungal amplicon"],
    "Metatranscriptomic": [
        "metatranscriptom", "rna-seq", "rnaseq", "transcriptom", "mrna",
    ],
    "Metabolomic": ["metabolom", "metabolite", "nmr", "lc-ms", "gc-ms"],
    "Proteomic": ["proteom", "mass spectrometry"],
    "Multiomic": ["multiomic", "multi-omic", "multiomics"],
    "Genome Isolate": ["genome isolate", "isolate genome"],
    "Full Length Operon": ["full length operon", "full-length 16s"],
}
# Inverted index for O(1) synonym lookup
_SYNONYM_INDEX = {
    s.lower(): dt
    for dt, syns in DATA_TYPE_SYNONYMS.items()
    for s in syns
}


def detect_data_types(keywords):
    """Return canonical Qiita data-type names whose synonyms appear in keywords."""
    found, seen = [], set()
    for kw in (keywords or []):
        for token in [kw.strip().lower()] + kw.strip().lower().split():
            dt = _SYNONYM_INDEX.get(token)
            if dt and dt not in seen:
                seen.add(dt)
                found.append(dt)
    return found


def build_data_type_filter(data_types, investigation_types=None):
    """Return (sql_snippet, params) for a data-type EXISTS filter, or (None, [])."""
    data_types = [d for d in (data_types or []) if d]
    if not data_types:
        return None, []
    placeholders = ",".join(["%s"] * len(data_types))
    params = list(data_types)
    inv_clause = ""
    if investigation_types:
        inv_ph = ",".join(["%s"] * len(investigation_types))
        inv_clause = f" AND pt.investigation_type IN ({inv_ph})"
        params.extend(investigation_types)
    sql = (
        f"EXISTS (SELECT 1 FROM qiita.study_prep_template spt"
        f" JOIN qiita.prep_template pt ON spt.prep_template_id = pt.prep_template_id"
        f" JOIN qiita.data_type dt ON pt.data_type_id = dt.data_type_id"
        f" WHERE spt.study_id = s.study_id"
        f" AND dt.data_type IN ({placeholders}){inv_clause})"
    )
    return sql, params


def build_where_from_plan(plan: dict) -> tuple:
    """Return (where_clause, params) for broad parameterized search from LLM plan.
    Keywords are morphologically expanded (mouse→mice) before building clauses.
    Searches title, abstract, alias, PI name, PI affiliation, lab contact (all OR'd)."""
    raw = [k.strip() for k in (plan.get("keywords") or []) if len(k.strip()) >= 2]
    keywords = expand_keyword_variants(raw)
    if not keywords:
        return "1=1", []
    # Literal % must be %% — psycopg2 treats bare % as placeholders when params are passed.
    clause = (
        "EXISTS (SELECT 1 FROM unnest(%s::text[]) AS kw"
        " WHERE s.study_title ILIKE ('%%' || kw || '%%')"
        " OR s.study_abstract ILIKE ('%%' || kw || '%%')"
        " OR s.study_alias ILIKE ('%%' || kw || '%%')"
        " OR sp_pi.name ILIKE ('%%' || kw || '%%')"
        " OR sp_pi.affiliation ILIKE ('%%' || kw || '%%')"
        " OR sp_lab.name ILIKE ('%%' || kw || '%%'))"
    )
    return clause, [keywords]


def build_relevance_score(keywords) -> tuple:
    """Return (sql_expr, params) scoring each study by keyword matches.

    Keywords are morphologically expanded first.
    Weights from RELEVANCE_WEIGHTS. Sums per-keyword scores via unnest()
    over one array param instead of repeating the CASE block per keyword.
    """
    kws = expand_keyword_variants(
        [k.strip() for k in (keywords or []) if len(k.strip()) >= 2]
    )
    if not kws:
        return "0", []
    w = RELEVANCE_WEIGHTS
    expr = (
        "(SELECT COALESCE(SUM("
        f"CASE WHEN s.study_title ILIKE ('%%' || kw || '%%') THEN {w['title']} ELSE 0 END"
        f" + CASE WHEN s.study_alias ILIKE ('%%' || kw || '%%') THEN {w['alias']} ELSE 0 END"
        f" + CASE WHEN sp_pi.name ILIKE ('%%' || kw || '%%') THEN {w['pi']} ELSE 0 END"
        f" + CASE WHEN s.study_abstract ILIKE ('%%' || kw || '%%') THEN {w['abstract']} ELSE 0 END"
        "), 0) FROM unnest(%s::text[]) AS kw)"
    )
    return expr, [kws]


def search_studies_with_sql(custom_sql_where="", params=None, limit=50, offset=0,
                            relevance_keywords=None,
                            data_types=None, investigation_types=None,
                            gold_only=False,
                            pi_filter_sql=None, pi_filter_params=None,
                            return_sql=False):
    """Search public studies with an optional topic WHERE clause, relevance ranking,
    and a data-type AND filter.

    Param binding order (psycopg2 left-to-right):
        score_params → data_type_filter_params → topic (WHERE) params → pi_filter_params
    """
    if params is None:
        params = []
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 50
    lim = max(1, min(150, lim))
    try:
        off = int(offset)
    except (TypeError, ValueError):
        off = 0
    off = max(0, off)

    # Relevance scoring: score params bind first (appear in SELECT)
    if relevance_keywords:
        score_expr, score_params = build_relevance_score(relevance_keywords)
        score_select = f", ({score_expr}) AS relevance"
        order_clause = "ORDER BY relevance DESC, num_samples DESC NULLS LAST, s.study_id"
    else:
        score_select = ""
        score_params = []
        order_clause = "ORDER BY s.study_id"

    # Data-type filter: AND-ed onto the topic WHERE (binds after score params)
    dt_sql, dt_params = build_data_type_filter(data_types, investigation_types)
    if dt_sql:
        topic_where = f"({custom_sql_where if custom_sql_where else '1=1'}) AND {dt_sql}"
    else:
        topic_where = custom_sql_where if custom_sql_where else "1=1"

    if gold_only:
        topic_where += " AND EXISTS (SELECT 1 FROM qiita.per_study_tags pst WHERE pst.study_id = s.study_id AND pst.study_tag = 'GOLD')"

    if pi_filter_sql:
        topic_where += f" AND ({pi_filter_sql})"

    full_params = score_params + dt_params + list(params) + list(pi_filter_params or [])

    logger.info(
        "[sql] search limit=%d offset=%d data_types=%s investigation_types=%s "
        "topic_params=%d score_params=%d total_params=%d pi_filter=%s",
        lim, off, data_types, investigation_types,
        len(params), len(score_params), len(full_params), bool(pi_filter_sql),
    )

    sql = f"""
    SELECT DISTINCT s.study_id, s.study_title, s.study_abstract,
           s.study_alias, s.metadata_complete,
           sp_pi.name as pi_name, sp_pi.email as pi_email,
           sp_pi.affiliation as pi_affiliation,
           sp_lab.name as lab_person_name,
           {_STUDY_COUNT_COLUMNS},
           EXISTS (
             SELECT 1 FROM qiita.per_study_tags pst
             WHERE pst.study_id = s.study_id AND pst.study_tag = 'GOLD'
           ) AS is_gold{score_select}
    FROM qiita.study s
    LEFT JOIN qiita.study_person sp_pi
        ON s.principal_investigator_id = sp_pi.study_person_id
    LEFT JOIN qiita.study_person sp_lab
        ON s.lab_person_id = sp_lab.study_person_id
    LEFT JOIN qiita.study_artifact sa ON s.study_id = sa.study_id
    LEFT JOIN qiita.artifact a ON sa.artifact_id = a.artifact_id
    LEFT JOIN qiita.visibility v ON a.visibility_id = v.visibility_id
    WHERE v.visibility = 'public'
      AND ({topic_where})
    {order_clause}
    LIMIT {lim}
    OFFSET {off}
    """

    if logger.isEnabledFor(logging.DEBUG):
        # Show first 500 chars of SQL and params count (not full params — too verbose)
        logger.debug("[sql] query_snippet=%r param_count=%d", sql[:500], len(full_params))

    results = pooled_fetchall(sql, full_params)

    row_count = len(results) if results else 0
    logger.info("[sql] rows_returned=%d", row_count)

    if not results:
        if return_sql:
            return [], sql.strip() + f"\n\n-- params ({len(full_params)}): {full_params!r}"
        return []

    studies = [{**_row_to_study_header(row), "is_gold": bool(row[12])} for row in results]

    if return_sql:
        return studies, sql.strip() + f"\n\n-- params ({len(full_params)}): {full_params!r}"
    return studies
