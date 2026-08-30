import logging

from flask import g, jsonify, request

from run import app
from config import GLOBAL_CHAT_SYSTEM_PROMPT
from helpers.agent_tool_schemas import TOOL_SCHEMAS
from helpers.chat_turn import stream_chat_turn
from store import (
    SCOPE_GLOBAL,
    append_global_chat_messages,
    create_global_chat,
    delete_global_chat,
    update_global_chat_title,
    set_global_chat_pinned,
    set_global_chat_archived,
    get_global_chat,
    global_chat_exists,
    list_global_chats,
    move_global_chat_to_project,
)
from helpers.llm_helpers import _sse
from helpers.pinned_context import _build_pinned_reports_context
from helpers.request_utils import (
    parse_chat_stream_body, load_history_for, sse_response,
    pin_response, unpin_response,
)

logger = logging.getLogger(__name__)


@app.route('/api/global-chats', methods=['GET'])
def api_list_global_chats():
    include_archived = request.args.get('include_archived') in ('1', 'true', 'True')
    return jsonify({'chats': list_global_chats(g.user_id, include_archived=include_archived)})


@app.route('/api/global-chats', methods=['POST'])
def api_create_global_chat():
    data  = request.get_json() or {}
    title = data.get('title')
    chat  = create_global_chat(g.user_id, title=title)
    if not chat:
        return jsonify({'error': 'Failed to create global chat'}), 500
    return jsonify(chat)


@app.route('/api/global-chats/<chat_id>', methods=['GET'])
def api_get_global_chat(chat_id):
    chat = get_global_chat(g.user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    return jsonify(chat)


@app.route('/api/global-chats/<chat_id>', methods=['PATCH'])
def api_update_global_chat(chat_id):
    data = request.get_json() or {}
    title, pinned, archived = data.get('title'), data.get('pinned'), data.get('archived')
    if title is None and pinned is None and archived is None:
        return jsonify({'error': 'title, pinned, or archived is required'}), 400
    if title is not None and (not isinstance(title, str) or not title.strip()):
        return jsonify({'error': 'title must be a non-empty string'}), 400

    result = {'chat_id': chat_id}
    if title is not None:
        r = update_global_chat_title(g.user_id, chat_id, title)
        if not r:
            return jsonify({'error': 'Chat not found'}), 404
        result['title'] = r['title']
    if pinned is not None:
        r = set_global_chat_pinned(g.user_id, chat_id, bool(pinned))
        if not r:
            return jsonify({'error': 'Chat not found'}), 404
        result['is_pinned'] = r['is_pinned']
    if archived is not None:
        r = set_global_chat_archived(g.user_id, chat_id, bool(archived))
        if not r:
            return jsonify({'error': 'Chat not found'}), 404
        result['is_archived'] = r['is_archived']
    return jsonify(result)


@app.route('/api/global-chats/<chat_id>', methods=['DELETE'])
def api_delete_global_chat(chat_id):
    delete_global_chat(g.user_id, chat_id)
    return jsonify({'ok': True})


@app.route('/api/global-chats/<chat_id>/move-to-project', methods=['POST'])
def api_move_global_chat_to_project(chat_id):
    data = request.get_json() or {}
    project_id = data.get('project_id')
    if not project_id:
        return jsonify({'error': 'project_id is required'}), 400
    chat = move_global_chat_to_project(g.user_id, chat_id, project_id)
    if not chat:
        return jsonify({'error': 'Chat or project not found'}), 404
    return jsonify(chat)


@app.route('/api/global-chats/<chat_id>/message/stream', methods=['POST'])
def api_global_chat_message_stream(chat_id):
    data        = request.get_json() or {}
    user_id     = g.user_id
    deep_search = bool(data.get("deep_search"))
    user_content, model, report_study_id, pin_study_ids, err_response = parse_chat_stream_body(data)
    if err_response is not None:
        return err_response

    chat = get_global_chat(user_id, chat_id, include_messages=False)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404

    full_msgs = load_history_for(chat_id, SCOPE_GLOBAL, user_content)

    def build_context():
        pinned_studies = chat.get("pinned_studies") or []
        pinned_ctx     = None
        if pinned_studies:
            yield _sse("step_start", {"name": "pinned_reports", "label": "Loading pinned study data…"})
            pinned_ctx = _build_pinned_reports_context(
                pinned_studies, model, tools_available=True)
            yield _sse("step_done", {"name": "pinned_reports", "label": "Pinned reports ready", "detail": f"{len(pinned_studies)} studies"})
            yield ': keepalive\n\n'
        return pinned_ctx

    return sse_response(lambda: stream_chat_turn(
        scope=SCOPE_GLOBAL, chat_id=chat_id, model=model, user_content=user_content,
        report_study_id=report_study_id, pin_study_ids=pin_study_ids,
        system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT, tools=TOOL_SCHEMAS,
        full_msgs=full_msgs, build_context=build_context, deep_search=deep_search,
        persist=lambda ac, up=None: append_global_chat_messages(
            user_id, chat_id, user_content, ac, assistant_ui_payload=up),
    ))


@app.route('/api/global-chats/<chat_id>/pinned/<int:study_id>', methods=['POST'])
def api_pin_global_chat_study(chat_id, study_id):
    if not global_chat_exists(g.user_id, chat_id):
        return jsonify({'error': 'Chat not found'}), 404
    return pin_response(chat_id, SCOPE_GLOBAL, study_id)


@app.route('/api/global-chats/<chat_id>/pinned/<int:study_id>', methods=['DELETE'])
def api_unpin_global_chat_study(chat_id, study_id):
    if not global_chat_exists(g.user_id, chat_id):
        return jsonify({'error': 'Chat not found'}), 404
    return unpin_response(chat_id, SCOPE_GLOBAL, study_id)
