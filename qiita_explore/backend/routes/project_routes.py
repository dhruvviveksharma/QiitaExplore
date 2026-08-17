import json

from flask import g, jsonify, request

from run import app, _bg_executor
from store import (
    PROJECT_STUDIES_CAP,
    add_study_to_project,
    create_project,
    delete_project,
    get_project,
    get_study_detail_cache,
    list_projects,
    remove_study_from_project,
    update_project_study_data,
    upsert_study_detail_cache,
)
from helpers.qiita_fetch import (
    _fetch_study_detail_from_qiita,
    _fetch_sample_context_text,
    _get_or_fetch_full_samples,
    _qiita_fetch,
    is_study_public,
)


def _enrich_study_in_project(project_id: str, study_id: int):
    """Background task: fetch num_samples + prep detail from Qiita and update project_studies."""
    cnt        = _qiita_fetch(
        "SELECT COUNT(*) FROM qiita.study_sample WHERE study_id = %s",
        [int(study_id)],
    )
    num_samples = cnt[0][0] if cnt else None

    preps = []
    try:
        cached = get_study_detail_cache(study_id)
        if cached:
            preps = json.loads(cached.get("preps_json") or "[]")
        else:
            preps, artifacts = _fetch_study_detail_from_qiita(study_id)
            upsert_study_detail_cache(study_id, json.dumps(preps), json.dumps(artifacts))
    except Exception:
        pass

    data_types = None
    num_preps  = None
    preps_json = None
    if preps:
        types      = sorted({p.get("data_type") for p in preps if p.get("data_type")})
        data_types = ", ".join(types) or None
        num_preps  = len(preps)
        preps_json = json.dumps(preps)

    update_project_study_data(
        project_id,
        study_id,
        data_types=data_types,
        num_samples=num_samples,
        num_preps=num_preps,
        preps_json=preps_json,
    )

    try:
        cached_detail = get_study_detail_cache(study_id)
        if not (cached_detail and cached_detail.get("samples_context")):
            samples_ctx = _fetch_sample_context_text(study_id)
            if samples_ctx:
                upsert_study_detail_cache(study_id, None, None, samples_context=samples_ctx)
    except Exception:
        pass


@app.route('/api/projects', methods=['GET'])
def api_list_projects():
    return jsonify({'projects': list_projects(g.user_id)})


@app.route('/api/projects', methods=['POST'])
def api_create_project():
    data = request.get_json() or {}
    name = (data.get('name') or 'Untitled').strip() or 'Untitled'
    proj = create_project(g.user_id, name)
    if not proj:
        return jsonify({'error': 'Failed to create project'}), 500
    return jsonify(proj)


@app.route('/api/projects/<project_id>', methods=['GET'])
def api_get_project(project_id):
    include_archived = request.args.get('include_archived') in ('1', 'true', 'True')
    proj = get_project(project_id, g.user_id, include_archived=include_archived)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404
    return jsonify(proj)


@app.route('/api/projects/<project_id>', methods=['DELETE'])
def api_delete_project(project_id):
    delete_project(project_id, g.user_id)
    return jsonify({'ok': True})


@app.route('/api/projects/<project_id>/studies', methods=['POST'])
def api_add_study(project_id):
    data    = request.get_json() or {}
    user_id = g.user_id
    study   = data.get('study')
    if not study or study.get('study_id') is None:
        return jsonify({'error': 'study with study_id required'}), 400

    if not is_study_public(study.get('study_id')):
        return jsonify({'error': 'Study is not public and cannot be added'}), 403

    proj = get_project(project_id, user_id)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404
    if len(proj.get('studies') or []) >= PROJECT_STUDIES_CAP:
        return jsonify({'error': f'Project has reached the maximum of {PROJECT_STUDIES_CAP} studies'}), 400

    proj = add_study_to_project(project_id, user_id, study)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404

    study_id = study.get('study_id')
    _bg_executor.submit(_enrich_study_in_project, project_id, int(study_id))
    return jsonify(proj)


@app.route('/api/projects/<project_id>/studies/enrich-all', methods=['POST'])
def api_enrich_all_studies(project_id):
    """Re-fetch enriched data for all studies in a project."""
    proj = get_project(project_id, g.user_id)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404

    studies = proj.get('studies') or []
    futures = []
    for s in studies:
        sid = s.get('study_id')
        if sid is not None:
            futures.append(_bg_executor.submit(_enrich_study_in_project, project_id, int(sid)))

    for f in futures:
        try:
            f.result(timeout=30)
        except Exception:
            pass

    updated = get_project(project_id, g.user_id)
    return jsonify({'ok': True, 'updated': len(futures), 'project': updated})


@app.route('/api/projects/<project_id>/studies/<int:study_id>', methods=['DELETE'])
def api_remove_study(project_id, study_id):
    proj = remove_study_from_project(project_id, g.user_id, study_id)
    if proj is None:
        return jsonify({'error': 'Project not found'}), 404
    return jsonify(proj)


@app.route('/api/projects/<project_id>/preload', methods=['POST'])
def api_project_preload(project_id):
    """Warm study_detail_cache.full_samples_json for every study in the project."""
    proj = get_project(project_id, g.user_id)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404
    queued = []
    for s in (proj.get('studies') or []):
        sid = s.get('study_id')
        if sid is None:
            continue
        try:
            sid_int = int(sid)
        except (TypeError, ValueError):
            continue
        _bg_executor.submit(_get_or_fetch_full_samples, sid_int, 500)
        queued.append(sid_int)
    return jsonify({'queued': queued})
