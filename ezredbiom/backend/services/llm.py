# backend/services/llm.py
import re

from config import GLOBAL_SEARCH_SQL_LIMIT_BROAD, GLOBAL_SEARCH_SQL_LIMIT_NARROW

_STOP_WORDS = frozenset({
    'find', 'search', 'show', 'get', 'give', 'list', 'what', 'which',
    'studies', 'study', 'about', 'with', 'the', 'a', 'an', 'in', 'on',
    'for', 'and', 'or', 'of', 'to', 'by', 'from', 'that', 'are', 'is',
    'have', 'has', 'data', 'using', 'related', 'some', 'all', 'any',
    'this', 'these', 'those', 'can', 'you', 'me', 'us',
})

_BREADTH_RE = re.compile(
    r'\b('
    r'many|all|several|overview|landscape|broad|extensive|multiple|various|'
    r'lots|wide|comprehensive|enumerate|examples?|catalog|survey|'
    r'anything|everything|explore|discovery|range|diverse'
    r')\b',
    re.I,
)


def _keyword_clause_sql():
    return (
        "(s.study_title ILIKE %s OR s.study_abstract ILIKE %s"
        " OR sp_pi.name ILIKE %s OR sp_pi.affiliation ILIKE %s)"
    )


def llm_query_to_sql(user_query: str) -> dict:
    """Convert natural language query to SQL WHERE clause via keyword extraction."""
    pi_match = re.search(
        r'\bby\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', user_query
    )

    words    = re.findall(r'\b[a-zA-Z0-9]+\b', user_query.lower())
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) >= 3]

    broad = bool(_BREADTH_RE.search(user_query)) or (len(keywords) >= 4)
    if broad:
        kw_use       = keywords[:6]
        keyword_join = " OR "
        search_limit = max(1, min(150, GLOBAL_SEARCH_SQL_LIMIT_BROAD))
    else:
        kw_use       = keywords[:2]
        keyword_join = " AND "
        search_limit = max(1, min(150, GLOBAL_SEARCH_SQL_LIMIT_NARROW))

    clauses: list = []
    params:  list = []

    if pi_match:
        name = pi_match.group(1)
        clauses.append("(sp_pi.name ILIKE %s OR sp_pi.affiliation ILIKE %s)")
        params += [f"%{name}%", f"%{name}%"]

    subclauses = []
    clause_sql = _keyword_clause_sql()
    for kw in kw_use:
        subclauses.append(clause_sql)
        like = f"%{kw}%"
        params += [like, like, like, like]

    if subclauses:
        clauses.append("(" + keyword_join.join(subclauses) + ")")

    return {
        "where_clause": " AND ".join(clauses) if clauses else "1=1",
        "params": params,
        "search_limit": search_limit,
        "match_mode": "broad" if broad else "narrow",
    }
