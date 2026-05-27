# backend/services/study_service.py
from qiita_db.sql_connection import TRN


def search_studies_from_plan(plan: dict):
    """Execute a search plan dict produced by llm_plan_query against Qiita."""
    keywords = [k.strip() for k in (plan.get("keywords") or []) if len(k.strip()) >= 3][:6]
    match_mode = "AND" if (plan.get("match_mode") or "AND").upper() == "AND" else "OR"

    if not keywords:
        return search_studies_with_sql("1=1", [], 50)

    clauses, params = [], []
    for kw in keywords:
        clauses.append("(s.study_title ILIKE %s OR s.study_abstract ILIKE %s)")
        params.extend([f"%{kw}%", f"%{kw}%"])

    where = f" {match_mode} ".join(clauses)
    studies = search_studies_with_sql(where, params, 50)

    kw_lower = [k.lower() for k in keywords]

    def _relevance(s):
        title    = (s.get("study_title")    or "").lower()
        abstract = (s.get("study_abstract") or "").lower()
        score = sum((2 if kw in title else 0) + (1 if kw in abstract else 0) for kw in kw_lower)
        return -score

    return sorted(studies, key=_relevance)


def search_studies_with_sql(custom_sql_where="", params=None, limit=50):
    """
    Search studies using custom SQL WHERE clause

    Parameters
    ----------
    custom_sql_where : str
        Custom WHERE clause (without the WHERE keyword)
    params : list
        Parameters for the SQL query
    limit : int
        Max rows (clamped 1–150)

    Returns
    -------
    list
        List of dictionaries with study information
    """
    if params is None:
        params = []
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 50
    lim = max(1, min(150, lim))

    with TRN:
        sql = f"""
        SELECT DISTINCT s.study_id, s.study_title, s.study_abstract,
               s.study_alias, s.metadata_complete,
               sp_pi.name as pi_name, sp_pi.email as pi_email,
               sp_pi.affiliation as pi_affiliation,
               sp_lab.name as lab_person_name,
               (SELECT COUNT(*)
                FROM qiita.study_sample ss
                WHERE ss.study_id = s.study_id) AS num_samples,
               (SELECT STRING_AGG(DISTINCT dt2.data_type, ', ')
                FROM qiita.study_prep_template spt2
                JOIN qiita.prep_template pt2 ON spt2.prep_template_id = pt2.prep_template_id
                JOIN qiita.data_type dt2 ON pt2.data_type_id = dt2.data_type_id
                WHERE spt2.study_id = s.study_id) AS data_types,
               (SELECT COUNT(DISTINCT spt3.prep_template_id)
                FROM qiita.study_prep_template spt3
                WHERE spt3.study_id = s.study_id) AS num_preps
        FROM qiita.study s
        LEFT JOIN qiita.study_person sp_pi
            ON s.principal_investigator_id = sp_pi.study_person_id
        LEFT JOIN qiita.study_person sp_lab
            ON s.lab_person_id = sp_lab.study_person_id
        LEFT JOIN qiita.study_artifact sa ON s.study_id = sa.study_id
        LEFT JOIN qiita.artifact a ON sa.artifact_id = a.artifact_id
        LEFT JOIN qiita.visibility v ON a.visibility_id = v.visibility_id
        WHERE v.visibility = 'public'
          AND ({custom_sql_where if custom_sql_where else '1=1'})
        ORDER BY s.study_id
        LIMIT {lim}
        """

        TRN.add(sql, params)
        results = TRN.execute_fetchindex()

    if not results:
        return []

    studies = []
    for row in results:
        studies.append({
            'study_id': row[0],
            'study_title': row[1],
            'study_abstract': row[2],
            'study_alias': row[3],
            'metadata_complete': row[4],
            'pi_name': row[5],
            'pi_email': row[6],
            'pi_affiliation': row[7],
            'lab_person_name': row[8],
            'num_samples': row[9],
            'data_types': row[10],
            'num_preps': row[11],
        })

    return studies