import logging

from flask import g, jsonify, request

from run import app
from config import PROJECT_CHAT_SYSTEM_PROMPT, context_budget_chars
from helpers.agent_tool_schemas import PROJECT_TOOL_SCHEMAS
from helpers.chat_turn import stream_chat_turn
from store import (
    SCOPE_PROJECT,
    append_chat_messages,
    create_chat,
    delete_chat,
    update_chat_title,
    set_chat_pinned,
    set_chat_archived,
    get_chat,
    project_chat_exists,
    get_project,
    get_project_studies_only,
    allowed_project_study_ids,
    move_chat_to_project,
    move_project_chat_to_global,
)
from helpers.llm_helpers import (
    _sse,
    _build_project_study_context,
    llm_chat,
)
from helpers.qiita_fetch import _detect_mentioned_study_ids
from helpers.pinned_context import _build_pinned_reports_context
from helpers.request_utils import (
    parse_chat_stream_body, load_history_for, sse_response,
    pin_response, unpin_response,
)

logger = logging.getLogger(__name__)


@app.route('/api/projects/<project_id>/chats', methods=['POST'])
def api_create_chat(project_id):
    data          = request.get_json() or {}
    user_id       = g.user_id
    proj          = get_project(project_id, user_id)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404
    first_message = (data.get('message') or data.get('first_message') or '').strip()
    model         = data.get('model')
    chat          = create_chat(project_id, user_id, first_message or data.get('title'))
    if first_message:
        study_ctx         = _build_project_study_context(proj, budget=context_budget_chars(model))
        assistant_content = llm_chat([{"role": "user", "content": first_message}], study_context_text=study_ctx,
                                      system_prompt=PROJECT_CHAT_SYSTEM_PROMPT, model=model)
        append_chat_messages(project_id, user_id, chat["chat_id"], first_message, assistant_content)
    chat = get_chat(project_id, user_id, chat["chat_id"])
    return jsonify(chat)


@app.route('/api/projects/<project_id>/chats/<chat_id>', methods=['GET'])
def api_get_chat(project_id, chat_id):
    chat = get_chat(project_id, g.user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    return jsonify(chat)


@app.route('/api/projects/<project_id>/chats/<chat_id>', methods=['PATCH'])
def api_update_chat(project_id, chat_id):
    data = request.get_json() or {}
    title, pinned, archived = data.get('title'), data.get('pinned'), data.get('archived')
    if title is None and pinned is None and archived is None:
        return jsonify({'error': 'title, pinned, or archived is required'}), 400
    if title is not None and (not isinstance(title, str) or not title.strip()):
        return jsonify({'error': 'title must be a non-empty string'}), 400

    result = {'chat_id': chat_id}
    if title is not None:
        r = update_chat_title(project_id, g.user_id, chat_id, title)
        if not r:
            return jsonify({'error': 'Chat not found'}), 404
        result['title'] = r['title']
    if pinned is not None:
        r = set_chat_pinned(project_id, g.user_id, chat_id, bool(pinned))
        if not r:
            return jsonify({'error': 'Chat not found'}), 404
        result['is_pinned'] = r['is_pinned']
    if archived is not None:
        r = set_chat_archived(project_id, g.user_id, chat_id, bool(archived))
        if not r:
            return jsonify({'error': 'Chat not found'}), 404
        result['is_archived'] = r['is_archived']
    return jsonify(result)


@app.route('/api/projects/<project_id>/chats/<chat_id>', methods=['DELETE'])
def api_delete_chat(project_id, chat_id):
    delete_chat(project_id, g.user_id, chat_id)
    return jsonify({'ok': True})


@app.route('/api/projects/<project_id>/chats/<chat_id>/move-to-project', methods=['POST'])
def api_move_chat_to_project(project_id, chat_id):
    data = request.get_json() or {}
    target_project_id = data.get('project_id')
    if not target_project_id:
        return jsonify({'error': 'project_id is required'}), 400
    chat = move_chat_to_project(g.user_id, chat_id, project_id, target_project_id)
    if not chat:
        return jsonify({'error': 'Chat or target project not found'}), 404
    return jsonify(chat)


@app.route('/api/projects/<project_id>/chats/<chat_id>/remove-from-project', methods=['POST'])
def api_remove_chat_from_project(project_id, chat_id):
    chat = move_project_chat_to_global(g.user_id, chat_id, project_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    return jsonify(chat)


@app.route('/api/projects/<project_id>/chats/<chat_id>/message/stream', methods=['POST'])
def api_chat_message_stream(project_id, chat_id):
    data    = request.get_json() or {}
    user_id = g.user_id
    user_content, model, report_study_id, pin_study_ids, err_response = parse_chat_stream_body(data)
    if err_response is not None:
        return err_response

    chat = get_chat(project_id, user_id, chat_id, include_messages=False)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404

    proj      = get_project_studies_only(project_id)
    full_msgs = load_history_for(chat_id, SCOPE_PROJECT, user_content)
    member_ids = allowed_project_study_ids(project_id)

    def build_context():
        num_proj_studies = len((proj or {}).get("studies") or [])
        yield _sse("step_start", {"name": "build_context", "label": "Loading study context…"})
        study_ctx = _build_project_study_context(proj, budget=context_budget_chars(model))
        yield _sse("step_done", {"name": "build_context", "label": "Study context ready", "detail": f"{num_proj_studies} studies"})
        yield ': keepalive\n\n'
        detected_ids   = _detect_mentioned_study_ids(user_content, proj)
        pinned_studies = chat.get("pinned_studies") or []
        deep_ids       = list(dict.fromkeys(detected_ids + [s for s in pinned_studies if s not in detected_ids]))
        deep_ctx = None
        if deep_ids:
            if detected_ids:
                ids_label = f"study {detected_ids[0]}" if len(detected_ids) == 1 else f"{len(detected_ids)} studies"
                fetch_label = f"Fetching data for {ids_label}…"
                done_label  = "Study data ready"
            else:
                fetch_label = "Loading pinned study data…"
                done_label  = "Pinned reports ready"
            yield _sse("step_start", {"name": "deep_context", "label": fetch_label})
            deep_ctx = _build_pinned_reports_context(
                deep_ids, model, tools_available=True,
                report_tool_name="get_project_study_report")
            yield _sse("step_done", {"name": "deep_context", "label": done_label, "detail": f"{len(deep_ids)} studies"})
            yield ': keepalive\n\n'
        return "\n\n".join(x for x in (study_ctx, deep_ctx) if x) or None

    def report_guard(study_id):
        if study_id not in member_ids:
            return (f"Study {study_id} is not part of this project. "
                    f"Add it via Browse first if you want its report here.")
        return None

    return sse_response(lambda: stream_chat_turn(
        scope=SCOPE_PROJECT, chat_id=chat_id, model=model, user_content=user_content,
        report_study_id=report_study_id, pin_study_ids=pin_study_ids,
        system_prompt=PROJECT_CHAT_SYSTEM_PROMPT, tools=PROJECT_TOOL_SCHEMAS,
        full_msgs=full_msgs, build_context=build_context, report_guard=report_guard,
        persist=lambda ac, up=None: append_chat_messages(
            project_id, user_id, chat_id, user_content, ac, assistant_ui_payload=up),
    ))


@app.route('/api/projects/<project_id>/chats/<chat_id>/pinned/<int:study_id>', methods=['POST'])
def api_pin_project_chat_study(project_id, chat_id, study_id):
    if not project_chat_exists(project_id, g.user_id, chat_id):
        return jsonify({'error': 'Chat not found'}), 404
    return pin_response(chat_id, SCOPE_PROJECT, study_id)


@app.route('/api/projects/<project_id>/chats/<chat_id>/pinned/<int:study_id>', methods=['DELETE'])
def api_unpin_project_chat_study(project_id, chat_id, study_id):
    if not project_chat_exists(project_id, g.user_id, chat_id):
        return jsonify({'error': 'Chat not found'}), 404
    return unpin_response(chat_id, SCOPE_PROJECT, study_id)
