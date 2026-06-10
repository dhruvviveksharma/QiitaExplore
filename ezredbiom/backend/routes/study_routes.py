import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import jsonify, request
from qiita_db.sql_connection import TRN

from run import app
from config import client, ALLOWED_MODELS, MODEL_METADATA, SAMPLE_SEARCH_DEEP_CANDIDATES
from services.llm import llm_query_to_sql
from services.study_service import search_studies_with_sql
from helpers.sample_search import search_studies_by_sample_meta
from store import get_study_detail_cache, upsert_study_detail_cache
from helpers.artifact_graph import fetch_artifact_graph
from helpers.qiita_fetch import (
    first_studies,
    is_study_public,
    _fetch_prep_metadata_summary,
    _fetch_study_samples,
    _fetch_study_detail_from_qiita,
    _fetch_sample_context_text,
)


@app.route('/api/studies/<int:study_id>/detail', methods=['GET'])
def api_study_detail(study_id):
    """Return prep templates, artifacts, and samples for a study (preps/artifacts cached)."""
    if not is_study_public(study_id):
        return jsonify({'error': 'Study not found or not public'}), 404
    cached = get_study_detail_cache(study_id)
    if cached:
        preps          = json.loads(cached.get("preps_json") or "[]")
        artifacts      = json.loads(cached.get("artifacts_json") or "[]")
        artifact_graph = json.loads(cached["artifact_graph_json"]) if cached.get("artifact_graph_json") else None
        cache_hit = True
    else:
        try:
            preps, artifacts = _fetch_study_detail_from_qiita(study_id)
            upsert_study_detail_cache(study_id, json.dumps(preps), json.dumps(artifacts))
            artifact_graph = None
            cache_hit = False
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500

    # Re-fetch if cached graph predates the filepaths feature
    if artifact_graph is not None:
        art_nodes = [n for n in artifact_graph if n.get("kind") == "artifact"]
        if art_nodes and "filepaths" not in art_nodes[0]:
            artifact_graph = None

    if artifact_graph is None:
        artifact_graph = fetch_artifact_graph(study_id)
        upsert_study_detail_cache(
            study_id, json.dumps(preps), json.dumps(artifacts),
            artifact_graph_json=json.dumps(artifact_graph),
        )

    prep_ids = [p.get("prep_template_id") for p in preps if p.get("prep_template_id") is not None]
    if prep_ids:
        with ThreadPoolExecutor(max_workers=min(len(prep_ids), 8)) as pool:
            meta_results = list(pool.map(_fetch_prep_metadata_summary, prep_ids))
        id_to_meta = dict(zip(prep_ids, meta_results))
        for prep in preps:
            pid = prep.get("prep_template_id")
            if pid is not None and pid in id_to_meta:
                prep.update(id_to_meta[pid])

    samples, total_samples = _fetch_study_samples(study_id, limit=200)

    if not (cached and cached.get("samples_context")):
        samples_ctx = _fetch_sample_context_text(study_id)
        if samples_ctx:
            upsert_study_detail_cache(
                study_id,
                json.dumps(preps),
                json.dumps(artifacts),
                samples_context=samples_ctx,
            )

    return jsonify({
        "study_id":       study_id,
        "preps":          preps,
        "artifacts":      artifacts,
        "artifact_graph": artifact_graph,
        "samples":        samples,
        "total_samples":  total_samples,
        "cached":         cache_hit,
    })


@app.route('/api/studies/<int:study_id>/samples/<path:sample_id>', methods=['GET'])
def api_sample_detail(study_id, sample_id):
    """Return all metadata fields for a single sample."""
    try:
        with TRN:
            TRN.add(
                f"""
                SELECT sample_values
                FROM qiita.sample_{study_id}
                WHERE sample_id = %s
                  AND sample_id <> 'qiita_sample_column_names'
                """,
                [sample_id],
            )
            rows = TRN.execute_fetchindex()
        if not rows:
            return jsonify({'error': 'Sample not found'}), 404
        fields = dict(rows[0][0])
        fields.pop('qiita_study_id', None)
        return jsonify({'sample_id': sample_id, 'fields': fields})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search', methods=['POST'])
def search():
    try:
        data        = request.get_json() or {}
        user_query  = data.get('query', '')
        deep_search = bool(data.get('deep_search', False))
        if not user_query:
            return jsonify({'error': 'Query is required'}), 400

        sql_query    = llm_query_to_sql(user_query)
        where_clause = sql_query.get('where_clause') or '1=1'
        params       = sql_query.get('params') if isinstance(sql_query.get('params'), list) else []
        lim          = sql_query.get("search_limit", 50) if isinstance(sql_query, dict) else 50
        kws = sql_query.get('keywords') or []
        text_results = search_studies_with_sql(
            custom_sql_where=where_clause, params=params, limit=lim,
            relevance_keywords=kws if kws else None,
        )
        if not isinstance(text_results, list):
            text_results = []

        if deep_search:
            keywords = sql_query.get('keywords') if isinstance(sql_query.get('keywords'), list) else []
            if not keywords:
                keywords = [w for w in user_query.split() if len(w) >= 2]
            seen_ids = {s['study_id'] for s in text_results}
            meta_results = search_studies_by_sample_meta(
                keywords, max_candidates=SAMPLE_SEARCH_DEEP_CANDIDATES
            )
            for s in meta_results:
                if s['study_id'] not in seen_ids:
                    seen_ids.add(s['study_id'])
                    text_results.append(s)

        return jsonify({
            'results':   text_results,
            'sql_query': sql_query,
            'count':     len(text_results),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _probe_model(model_name):
    start = time.time()
    try:
        client.chat.completions.create(
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
