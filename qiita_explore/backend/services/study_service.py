# backend/services/study_service.py
import logging

from helpers.pg_pool import pooled_fetchall
from helpers.qiita_fetch import (
    _PUBLIC_ARTIFACT_EXISTS, _STUDY_COUNT_COLUMNS, _row_to_study_header,
)
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

# Bidirectional domain-concept groups (a vetted subset of the agent-prompt
# slot examples in config.py): a search naming any member also matches the
# rest. Every member must be >= 3 chars and substring-safe — bare "GI" as
# ILIKE '%gi%' would match "fungi"/"aging"; "colon" would match "colonization".
DOMAIN_SYNONYM_GROUPS = [
    ["gut", "intestine", "intestinal", "GI tract", "cecum",
     "feces", "stool", "fecal"],
    ["microbiome", "microbiota"],
    ["soil", "rhizosphere", "sediment"],
    ["FMT", "fecal microbiota transplant", "fecal transplant", "stool transplant"],
    ["antibiotic", "antibiotics", "antimicrobial"],
    ["human", "homo sapiens"],
    ["infant", "baby", "neonatal", "neonate"],
    ["obesity", "obese"],
    ["IBD", "inflammatory bowel disease", "crohn", "colitis"],
    ["cancer", "tumor", "tumour"],
]
_DOMAIN_INDEX = {}  # lowercased member -> list of groups (a member may sit in several)
for _group in DOMAIN_SYNONYM_GROUPS:
    for _term in _group:
        _DOMAIN_INDEX.setdefault(_term.lower(), []).append(_group)


def expand_keyword_variants(keywords):
    """Add plural/singular variants (mouse↔mice) and domain-synonym group
    members (gut↔intestine↔stool, …). Caps at 80.

    Three tiers, each fully completed before the next, so direct user terms
    can never be pushed past the cap by their own variants or synonyms:
    (1) every cleaned input term, (2) each term's morphological variant,
    (3) domain-group members (looked up by whole phrase AND per-token, so
    "gut microbiome" pulls in "intestine" and "microbiota"). Domain members
    get no plural expansion — substring ILIKE already makes "tumor" match
    "tumors". Dedup is case-insensitive (ILIKE makes case duplicates pure
    waste).
    """
    seen, expanded = set(), []

    def _add(term):
        tl = term.lower()
        if tl not in seen:
            seen.add(tl)
            expanded.append(term)

    cleaned = [kw.strip() for kw in (keywords or []) if kw and kw.strip()]

    for kw in cleaned:
        _add(kw)

    for kw in cleaned:
        kl = kw.lower()
        if kl in _IRREGULAR_VARIANTS:
            _add(_IRREGULAR_VARIANTS[kl])
        elif not kl.endswith("s") and len(kl) > 2:
            _add(kw + "s")

    for kw in cleaned:
        kl = kw.lower()
        for token in [kl] + kl.split():
            for group in _DOMAIN_INDEX.get(token, []):
                for member in group:
                    _add(member)
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


def build_tag_filter(tags):
    """Return (sql_snippet, params) for a qiita.per_study_tags EXISTS filter,
    or (None, []). Structurally identical to build_data_type_filter — no
    fixed vocabulary is assumed (the only tag ever referenced anywhere in
    this codebase before this was the literal 'GOLD'); any tag value the
    caller supplies is filtered on as-is."""
    tags = [t for t in (tags or []) if t]
    if not tags:
        return None, []
    placeholders = ",".join(["%s"] * len(tags))
    sql = (
        f"EXISTS (SELECT 1 FROM qiita.per_study_tags pst"
        f" WHERE pst.study_id = s.study_id AND pst.study_tag IN ({placeholders}))"
    )
    return sql, list(tags)


_KEYWORD_MATCH_CONDITION = "(rel.relevance > 0 OR rel.aux_match)"


def build_keyword_lateral(keywords) -> tuple:
    """Return (lateral_sql, params): one CROSS JOIN LATERAL computing
    rel.relevance (4 scored fields, weights from RELEVANCE_WEIGHTS) and
    rel.aux_match (PI affiliation + lab-contact name) in a single pass over
    unnest(keywords) — one array bind serving both scoring and matching.

    Keywords must be pre-expanded (expand_keyword_variants at the caller) —
    no re-expansion here. Terms under 2 chars are dropped, as the replaced
    builders (build_where_from_plan / build_relevance_score) did.
    Returns ("", []) when nothing usable remains.

    rel.relevance > 0  <=>  some kw hit title/alias/pi.name/abstract (all
    weights positive); rel.aux_match covers the 2 extra fields the old
    6-field match clause had. BOOL_OR ignores NULL inputs and yields NULL
    over an all-NULL set, so _KEYWORD_MATCH_CONDITION excludes exactly the
    rows the old EXISTS excluded (NULL LEFT-JOINed sp_pi/sp_lab included).
    """
    kws = [k.strip() for k in (keywords or []) if len(k.strip()) >= 2]
    if not kws:
        return "", []
    w = RELEVANCE_WEIGHTS
    # Literal % must be %% — psycopg2 treats bare % as placeholders when params are passed.
    sql = (
        "CROSS JOIN LATERAL (\n"
        "        SELECT COALESCE(SUM(\n"
        f"            CASE WHEN s.study_title ILIKE ('%%' || kw || '%%') THEN {w['title']} ELSE 0 END\n"
        f"          + CASE WHEN s.study_alias ILIKE ('%%' || kw || '%%') THEN {w['alias']} ELSE 0 END\n"
        f"          + CASE WHEN sp_pi.name ILIKE ('%%' || kw || '%%') THEN {w['pi']} ELSE 0 END\n"
        f"          + CASE WHEN s.study_abstract ILIKE ('%%' || kw || '%%') THEN {w['abstract']} ELSE 0 END\n"
        "        ), 0) AS relevance,\n"
        "        BOOL_OR(sp_pi.affiliation ILIKE ('%%' || kw || '%%')\n"
        "             OR sp_lab.name ILIKE ('%%' || kw || '%%')) AS aux_match\n"
        "        FROM unnest(%s::text[]) AS kw\n"
        "    ) rel"
    )
    return sql, [kws]


def search_studies_with_sql(custom_sql_where="", params=None, limit=50,
                            relevance_keywords=None, match_keywords=None,
                            data_types=None, investigation_types=None,
                            tags=None,
                            pi_filter_sql=None, pi_filter_params=None,
                            return_sql=False):
    """Search public studies with an optional topic WHERE clause, relevance ranking,
    and a data-type AND filter.

    match_keywords = filter AND score (agent path); relevance_keywords =
    score-only (browse path, which brings its own custom WHERE). If both are
    given, match_keywords wins — they share one LATERAL/one array bind.
    Both are expected pre-expanded (expand_keyword_variants at the caller).

    Param binding order (psycopg2 left-to-right, matching the rendered SQL):
        kw_params (FROM: CROSS JOIN LATERAL unnest(%s::text[]))
        → topic (WHERE custom) params → data_type_filter_params
        → tag_filter_params → pi_filter_params

    The WHERE renders as "(custom_sql_where) AND dt_sql AND tag_sql AND
    (pi_filter_sql)", so the custom clause's own placeholders occur first in
    the WHERE text — the params list must match that. An earlier version
    bound dt_params before the topic params, which fed a bare data-type
    string into a keyword clause's unnest(%s::text[]) — Postgres rejected
    every keyword+data-type search with `malformed array literal:
    "Metagenomic"` (TKT-055, confirmed live 2026-08-30).
    """
    if params is None:
        params = []
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 50
    lim = max(1, min(150, lim))

    # One LATERAL does scoring and (optionally) matching — single array bind.
    kws = match_keywords or relevance_keywords
    lateral_sql, kw_params = build_keyword_lateral(kws) if kws else ("", [])
    if lateral_sql:
        score_select = ", rel.relevance AS relevance"
        order_clause = "ORDER BY relevance DESC, num_samples DESC NULLS LAST, s.study_id"
    else:
        kw_params = []
        score_select = ""
        order_clause = "ORDER BY s.study_id"

    topic_where = custom_sql_where if custom_sql_where else "1=1"
    dt_sql, dt_params = build_data_type_filter(data_types, investigation_types)
    if dt_sql:
        topic_where = f"({topic_where}) AND {dt_sql}"

    tag_sql, tag_params = build_tag_filter(tags)
    if tag_sql:
        topic_where += f" AND {tag_sql}"

    if pi_filter_sql:
        topic_where += f" AND ({pi_filter_sql})"

    if lateral_sql and match_keywords:
        # No params of its own — leading keeps the WHERE readable.
        if topic_where == "1=1":
            topic_where = _KEYWORD_MATCH_CONDITION
        else:
            topic_where = f"{_KEYWORD_MATCH_CONDITION} AND ({topic_where})"

    full_params = kw_params + list(params) + dt_params + tag_params + list(pi_filter_params or [])

    logger.info(
        "[sql] search limit=%d data_types=%s investigation_types=%s "
        "topic_params=%d kw_params=%d total_params=%d pi_filter=%s",
        lim, data_types, investigation_types,
        len(params), len(kw_params), len(full_params), bool(pi_filter_sql),
    )

    sql = f"""
    SELECT s.study_id, s.study_title, s.study_abstract,
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
    {lateral_sql}
    WHERE {_PUBLIC_ARTIFACT_EXISTS}
      AND ({topic_where})
    {order_clause}
    LIMIT {lim}
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
