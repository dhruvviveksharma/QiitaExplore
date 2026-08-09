"""Unified relevance scoring and PI resolution for study search."""

from helpers.pg_pool import pooled_fetchall

RELEVANCE_WEIGHTS = {
    "title": 30,
    "abstract": 10,
    "alias": 15,
    "pi": 20,
    "sample_per_kw": 1,
}


def score_study_text_fields(study: dict, keywords: list) -> int:
    """Score a study header dict by keyword matches in title/abstract/alias/PI."""
    title = (study.get("study_title") or "").lower()
    abstract = (study.get("study_abstract") or "").lower()
    alias = (study.get("study_alias") or "").lower()
    pi_name = (study.get("pi_name") or "").lower()
    w = RELEVANCE_WEIGHTS
    total = 0
    for kw in (keywords or []):
        kl = kw.lower()
        if kl in title:
            total += w["title"]
        if kl in abstract:
            total += w["abstract"]
        if kl in alias:
            total += w["alias"]
        if kl in pi_name:
            total += w["pi"]
    return total


def compute_total_relevance(study: dict, keywords: list, sample_kw_hits: int) -> int:
    """Sum text-field score and sample-metadata keyword hits."""
    return score_study_text_fields(study, keywords) + sample_kw_hits * RELEVANCE_WEIGHTS["sample_per_kw"]


def study_matches_pi(study: dict, resolved_pis: list) -> bool:
    """True if study PI name/affiliation matches any resolved PI record."""
    if not resolved_pis:
        return True
    pi_name = (study.get("pi_name") or "").lower()
    pi_aff = (study.get("pi_affiliation") or "").lower()
    haystack = f"{pi_name} {pi_aff}"
    for r in resolved_pis:
        name = (r.get("name") or "").lower()
        if not name:
            continue
        if name in haystack or pi_name in name:
            return True
        for part in name.split():
            if len(part) > 2 and part in haystack:
                return True
    return False


def build_pi_required_filter(resolved_pis: list) -> tuple:
    """Return (sql_snippet, params) AND filter for studies by resolved PI persons."""
    if not resolved_pis:
        return None, []
    names = [r.get("name") for r in resolved_pis if r.get("name")]
    if not names:
        return None, []
    sql = (
        "EXISTS (SELECT 1 FROM unnest(%s::text[]) AS pi_name"
        " WHERE sp_pi.name ILIKE ('%' || pi_name || '%')"
        " OR sp_pi.affiliation ILIKE ('%' || pi_name || '%'))"
    )
    return sql, [names]


def resolve_pi(pi_texts: list) -> list:
    """Look up PI persons in qiita.study_person by name/affiliation ILIKE."""
    texts = [t.strip() for t in (pi_texts or []) if t and str(t).strip()]
    if not texts:
        return []
    sql = """
        SELECT DISTINCT study_person_id, name, affiliation
        FROM qiita.study_person
        WHERE EXISTS (
            SELECT 1 FROM unnest(%s::text[]) AS pi
            WHERE name ILIKE ('%' || pi || '%') OR affiliation ILIKE ('%' || pi || '%')
        )
    """
    rows = pooled_fetchall(sql, [texts])
    if not rows:
        return []
    return [
        {"study_person_id": row[0], "name": row[1], "affiliation": row[2]}
        for row in rows
    ]


def normalize_entities(args: dict) -> list:
    """Merge entities[] and legacy project_or_pi into a normalized entity list."""
    entities = []
    for e in (args.get("entities") or []):
        if isinstance(e, dict) and e.get("text"):
            entities.append({
                "text": str(e["text"]).strip(),
                "type": e.get("type") or "unknown",
            })
    for t in (args.get("project_or_pi") or []):
        t = str(t).strip()
        if t and not any(x["text"] == t for x in entities):
            entities.append({"text": t, "type": "unknown"})
    return entities


def prepare_pi_filter(entities: list) -> tuple:
    """Return (pi_texts, resolved, veto_applied, applied_filters_pi dict)."""
    pi_texts = [e["text"] for e in entities if e.get("type") == "pi"]
    resolved = resolve_pi(pi_texts) if pi_texts else []
    veto_applied = bool(pi_texts and resolved)
    applied = {
        "input": pi_texts,
        "resolved": [r["name"] for r in resolved],
        "veto_applied": veto_applied,
    }
    return pi_texts, resolved, veto_applied, applied


def pi_detail_suffix(applied_pi: dict) -> str:
    """Short PI clause for tool detail / UI banners."""
    if not applied_pi.get("input"):
        return ""
    if applied_pi.get("veto_applied"):
        names = ", ".join(applied_pi.get("resolved") or [])
        return f" · PI: {names} ✓"
    label = applied_pi["input"][0] if len(applied_pi["input"]) == 1 else ", ".join(applied_pi["input"])
    return f" · PI '{label}' not found — unfiltered"


def apply_pi_veto(studies: list, resolved_pis: list, veto_applied: bool) -> list:
    """Drop studies not matching resolved PIs when veto is active."""
    if not veto_applied:
        return studies
    return [s for s in studies if study_matches_pi(s, resolved_pis)]


def finalize_search_results(studies, keywords, resolved_pis=None, veto_applied=False,
                            limit=None, pool_size=16):
    """Score sample metadata layer, sort by relevance, apply PI veto, optional trim."""
    if not studies or not keywords:
        ranked = apply_pi_veto(list(studies or []), resolved_pis or [], veto_applied)
        return ranked[:limit] if limit else ranked
    from helpers.sample_search import score_studies_sample_layer
    sample_scores = score_studies_sample_layer(
        [s["study_id"] for s in studies], keywords, pool_size=pool_size,
    )
    for s in studies:
        s["relevance"] = compute_total_relevance(
            s, keywords, sample_scores.get(s["study_id"], 0),
        )
    studies.sort(
        key=lambda s: (s.get("relevance", 0), s.get("num_samples") or 0),
        reverse=True,
    )
    studies = apply_pi_veto(studies, resolved_pis or [], veto_applied)
    return studies[:limit] if limit else studies
