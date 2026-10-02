import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import jsonify, request

from run import app
from config import (
    ALLOWED_MODELS, GLOBAL_SEARCH_SQL_LIMIT_BROAD, MODEL_METADATA,
    SAMPLE_SEARCH_DEEP_CANDIDATES, get_client,
)
from services.llm import browse_query_to_sql
from services.browse_filters import (
    parse_browse_filters, build_browse_filter_where, study_passes_browse_filters,
    get_search_facets,
)
from services.study_service import search_studies_with_sql, expand_keyword_variants
from services.relevance import build_pi_required_filter, finalize_search_results
from helpers.sample_search import search_studies_by_sample_meta
from store.crud import get_setting, set_setting
from helpers import study_detail
from helpers.study_samples import fetch_prep_samples, fetch_sample_fields, prep_groups
from helpers.qiita_fetch import first_studies, is_study_public


@app.route('/api/studies/<int:study_id>', methods=['GET'])
def api_get_study(study_id):
    """Return a single study's title/abstract/PI metadata by id alone."""
    if not is_study_public(study_id):
        return jsonify({'error': 'Study not found or not public'}), 404
    results = search_studies_with_sql(custom_sql_where="s.study_id = %s", params=[study_id], limit=1)
    if not results:
        return jsonify({'error': 'Study not found'}), 404
    return jsonify(results[0])


@app.route('/api/studies/<int:study_id>/detail', methods=['GET'])
def api_study_detail(study_id):
    """Return prep templates, artifacts, and samples for a study (helpers/study_detail.py)."""
    if not is_study_public(study_id):
        return jsonify({'error': 'Study not found or not public'}), 404
    try:
        return jsonify(study_detail.get_study_detail(study_id))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/studies/<int:study_id>/sample-preps', methods=['GET'])
def api_study_sample_preps(study_id):
    """Group headers for "Group by prep": [{prep_id, data_type, num_samples}] for
    every prep of the study, plus a prep_id=None entry for samples in no prep."""
    if not is_study_public(study_id):
        return jsonify({'error': 'Study not found or not public'}), 404
    return jsonify({"groups": prep_groups(study_id)})


@app.route('/api/studies/<int:study_id>/preps/<prep_id>/samples', methods=['GET'])
def api_study_prep_samples(study_id, prep_id):
    """One prep's samples (prep_id "none" = samples in no prep), fetched when the
    group is expanded. ?limit= (1-500, default 500); `total` is the full count."""
    if not is_study_public(study_id):
        return jsonify({'error': 'Study not found or not public'}), 404
    try:
        pid = None if prep_id == "none" else int(prep_id)
        limit = min(500, max(1, int(request.args.get("limit", 500))))
    except ValueError:
        return jsonify({'error': 'Invalid prep id or limit'}), 400
    samples, total = fetch_prep_samples(study_id, pid, limit)
    return jsonify({"prep_id": pid, "samples": samples, "total": total, "limit": limit})


@app.route('/api/studies/<int:study_id>/samples/<path:sample_id>', methods=['GET'])
def api_sample_detail(study_id, sample_id):
    """Return all metadata fields for a single sample."""
    if not is_study_public(study_id):
        return jsonify({'error': 'Study not found or not public'}), 404
    try:
        fields = fetch_sample_fields(study_id, sample_id)
        if fields is None:
            return jsonify({'error': 'Sample not found'}), 404
        return jsonify({'sample_id': sample_id, 'fields': fields})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search', methods=['POST'])
def search():
    try:
        data        = request.get_json() or {}
        user_query  = (data.get('query') or '').strip()
        deep_search = bool(data.get('deep_search', True))
        try:
            filters = parse_browse_filters(data.get('filters'))
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        # Facet filters alone are a valid search (filter-only browsing).
        if not user_query and not filters:
            return jsonify({'error': 'Query is required'}), 400
        f = filters or {}
        f_pis, f_dts   = f.get('pis') or [], f.get('data_types') or []
        f_ymin, f_ymax = f.get('year_min'), f.get('year_max')

        sql_query    = browse_query_to_sql(user_query)
        where_clause = sql_query.get('where_clause') or '1=1'
        params       = sql_query.get('params') if isinstance(sql_query.get('params'), list) else []
        lim          = sql_query.get("search_limit", 50) if isinstance(sql_query, dict) else 50
        kws = sql_query.get('keywords') or []
        expanded_kws = expand_keyword_variants(kws) if kws else []
        # PI / year facets bind in the topic slot — ANDed into the custom
        # WHERE together with its params, so search_studies_with_sql's param
        # order is untouched. Data types use its existing data_types= kwarg.
        f_sql, f_params = build_browse_filter_where(f_pis, f_ymin, f_ymax)
        if f_sql:
            where_clause = f"({where_clause}) AND {f_sql}"
            params = list(params) + f_params
        if not kws and filters:
            # Nothing to rank by — show the broad cap (the grid has no paging).
            lim = GLOBAL_SEARCH_SQL_LIMIT_BROAD
        resolved_pis = sql_query.get('resolved_pis') or []
        veto_applied = bool(sql_query.get('veto_applied'))
        pi_sql, pi_params = build_pi_required_filter(resolved_pis) if veto_applied else (None, [])
        # Exact-ID boost only matters when text keywords compete for the
        # ranking; a pure-ID query is already isolated by its WHERE clause.
        study_ids = sql_query.get('study_ids') or []
        id_only   = bool(sql_query.get('id_only'))
        boost_ids = study_ids if (study_ids and expanded_kws) else None
        phrase    = sql_query.get('phrase')
        # Detected from the query text (e.g. "gold studies") plus any explicit
        # tags an API caller supplies directly — deduped, order preserved.
        explicit_tags = [t for t in (data.get('tags') or []) if isinstance(t, str) and t]
        tags = list(dict.fromkeys((sql_query.get('tags') or []) + explicit_tags)) or None

        text_results = search_studies_with_sql(
            custom_sql_where=where_clause, params=params, limit=lim,
            relevance_keywords=expanded_kws if expanded_kws else None,
            data_types=f_dts or None,
            tags=tags,
            pi_filter_sql=pi_sql,
            pi_filter_params=pi_params,
            boost_study_ids=boost_ids,
            title_phrase=phrase,
        )
        if not isinstance(text_results, list):
            text_results = []

        # A pasted study ID has nothing to gain from probing 500 studies'
        # sample metadata for a digit string (sample IDs are full of them);
        # a filter-only browse has no terms to probe with at all.
        probe_kws = expanded_kws or [w for w in user_query.split() if len(w) >= 2]
        if deep_search and not id_only and probe_kws:
            seen_ids = {s['study_id'] for s in text_results}
            # Facet PIs narrow the candidate set (ILIKE, a superset of the
            # exact match); study_passes_browse_filters below is the exact gate.
            cand_pis = list(resolved_pis if veto_applied else []) + [{"name": p} for p in f_pis]
            meta_results = search_studies_by_sample_meta(
                probe_kws,
                data_types=f_dts or None,
                max_candidates=SAMPLE_SEARCH_DEEP_CANDIDATES,
                resolved_pis=cand_pis or None,
                year_min=f_ymin, year_max=f_ymax,
            )
            for s in meta_results:
                if s['study_id'] not in seen_ids:
                    seen_ids.add(s['study_id'])
                    text_results.append(s)

        if filters:
            text_results = [
                s for s in text_results
                if study_passes_browse_filters(s, f_pis, f_dts, f_ymin, f_ymax)
            ]

        if expanded_kws and text_results:
            text_results = finalize_search_results(
                text_results, expanded_kws,
                resolved_pis=resolved_pis, veto_applied=veto_applied,
                boost_study_ids=boost_ids, title_phrase=phrase,
            )

        applied_filters = sql_query.get('applied_filters') or {}

        return jsonify({
            'results':   text_results,
            'sql_query': sql_query,
            'applied_filters': applied_filters,
            'filters':   filters,
            'count':     len(text_results),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/facets', methods=['GET'])
def api_search_facets():
    """PI / year-added / data-type option lists for the Browse filter pickers."""
    try:
        return jsonify(get_search_facets())
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _probe_model(model_name):
    start = time.time()
    try:
        c, provider = get_client(model_name)
        if provider == "anthropic":
            c.with_options(timeout=15.0).messages.create(
                model=model_name,
                max_tokens=1,
                messages=[{"role": "user", "content": "Hi"}],
            )
        else:
            c.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=1,
                timeout=15,
            )
        return model_name, "ok", int((time.time() - start) * 1000)
    except Exception:
        return model_name, "down", int((time.time() - start) * 1000)


@app.route('/api/systems', methods=['GET'])
def api_systems():
    results = {}
    with ThreadPoolExecutor(max_workers=len(ALLOWED_MODELS)) as pool:
        futures = {pool.submit(_probe_model, m): m for m in ALLOWED_MODELS}
        for f in as_completed(futures):
            name, status, ms = f.result()
            results[name] = {"status": status, "latency_ms": ms, **MODEL_METADATA.get(name, {})}
    ordered = sorted(
        results.items(),
        key=lambda x: (0 if x[1].get("tier") == "main" else 1, x[0]),
    )
    return jsonify([{"name": k, **v} for k, v in ordered])


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    key = get_setting('anthropic_api_key') or ''
    return jsonify({"anthropic_key_set": bool(key)})


@app.route('/api/settings', methods=['POST'])
def api_post_settings():
    data = request.get_json(force=True) or {}
    raw = (data.get('anthropic_api_key') or '').strip()
    if raw:
        set_setting('anthropic_api_key', raw)
    return jsonify({"ok": True})


@app.route('/api/studies/first', methods=['GET'])
def api_first_studies():
    try:
        limit = request.args.get('limit', 20)
        rows  = first_studies(limit=limit)
        return jsonify({
            "results": rows,
            "count":   len(rows),
            "limit":   max(1, min(100, int(limit) if str(limit).isdigit() else 20)),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
