# backend/services/llm.py
import re
from typing import Optional

from config import GLOBAL_SEARCH_SQL_LIMIT_BROAD, GLOBAL_SEARCH_SQL_LIMIT_NARROW
from services.relevance import resolve_pi, build_pi_required_filter

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


def _extract_pi_text(user_query: str) -> Optional[str]:
    """Return PI name text from common browse-box patterns, or None."""
    m = re.search(r'\bby\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', user_query)
    if m:
        return m.group(1)
    m = re.search(r"\bPI\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", user_query)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)'s\s+studies\b", user_query)
    if m:
        return m.group(1)
    return None


def llm_query_to_sql(user_query: str) -> dict:
    """Convert natural language query to SQL WHERE clause via keyword extraction."""
    pi_text = _extract_pi_text(user_query)
    pi_texts = [pi_text] if pi_text else []
    resolved = resolve_pi(pi_texts) if pi_texts else []
    veto_applied = bool(pi_texts and resolved)
    applied_pi = {
        "input": pi_texts,
        "resolved": [r["name"] for r in resolved],
        "veto_applied": veto_applied,
    }

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

    text_clauses: list = []
    params:       list = []

    if veto_applied:
        pi_sql, pi_params = build_pi_required_filter(resolved)
        if pi_sql:
            text_clauses.append(pi_sql)
            params.extend(pi_params)

    subclauses = []
    clause_sql = _keyword_clause_sql()
    for kw in kw_use:
        subclauses.append(clause_sql)
        like = f"%{kw}%"
        params += [like, like, like, like]

    if subclauses:
        text_clauses.append("(" + keyword_join.join(subclauses) + ")")

    text_where = " AND ".join(text_clauses) if text_clauses else ""

    numeric_ids = [int(n) for n in re.findall(r'\b\d+\b', user_query)]
    if numeric_ids and text_where:
        where_clause = f"({text_where}) OR s.study_id = ANY(%s)"
        params.append(numeric_ids)
    elif numeric_ids:
        where_clause = "s.study_id = ANY(%s)"
        params.append(numeric_ids)
    else:
        where_clause = text_where or "1=1"

    return {
        "where_clause": where_clause,
        "params": params,
        "search_limit": search_limit,
        "match_mode": "broad" if broad else "narrow",
        "keywords": kw_use,
        "applied_filters": {"pi": applied_pi} if pi_texts else {},
        "resolved_pis": resolved if veto_applied else [],
        "veto_applied": veto_applied,
    }
