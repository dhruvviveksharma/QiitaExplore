from flask import g, jsonify, request

from run import app
from config import PROJECT_CHAT_AGENT_SYSTEM_PROMPT
from helpers.chat_turn import stream_chat_turn
from helpers.pi_client import delete_session as pi_delete_session
from helpers.pin_flow import pin_toggle
from store import (
    SCOPE_PROJECT,
    append_chat_messages,
    create_chat,
    delete_chat,
    get_chat,
    get_project,
    get_project_studies_only,
)
from helpers.llm_helpers import _sse, _build_workspace_manifest
from helpers.qiita_fetch import _build_pinned_reports_context, _detect_mentioned_study_ids
from helpers.request_utils import parse_chat_stream_body, sse_response


def _stream_deep_context(user_content, proj, chat):
    """Fetch full reports for studies the user named plus anything pinned,
    emitting the deep_context step around it. Returns the context block (or None)."""
    detected_ids   = _detect_mentioned_study_ids(user_content, proj)
    pinned_studies = chat.get("pinned_studies") or []
    deep_ids       = list(dict.fromkeys(detected_ids + [s for s in pinned_studies if s not in detected_ids]))
    if not deep_ids:
        return None

    if detected_ids:
        ids_label   = f"study {detected_ids[0]}" if len(detected_ids) == 1 else f"{len(detected_ids)} studies"
        fetch_label = f"Fetching data for {ids_label}…"
        done_label  = "Study data ready"
    else:
        fetch_label = "Loading pinned study data…"
        done_label  = "Pinned reports ready"

    yield _sse("step_start", {"name": "deep_context", "label": fetch_label})
    deep_ctx = _build_pinned_reports_context(deep_ids)
    yield _sse("step_done", {"name": "deep_context", "label": done_label, "detail": f"{len(deep_ids)} studies"})
    yield ': keepalive\n\n'
    return deep_ctx


@app.route('/api/projects/<project_id>/chats', methods=['POST'])
def api_create_chat(project_id):
    data    = request.get_json() or {}
    user_id = g.user_id
    proj    = get_project(project_id, user_id)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404
    chat = create_chat(project_id, user_id, data.get('title'))
    return jsonify(chat)


@app.route('/api/projects/<project_id>/chats/<chat_id>', methods=['GET'])
def api_get_chat(project_id, chat_id):
    chat = get_chat(project_id, g.user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    return jsonify(chat)


@app.route('/api/projects/<project_id>/chats/<chat_id>', methods=['DELETE'])
def api_delete_chat(project_id, chat_id):
    delete_chat(project_id, g.user_id, chat_id)
    # Best-effort: delete_session() no-ops when pi isn't configured at all.
    pi_delete_session(scope=SCOPE_PROJECT, chat_id=chat_id, user_id=g.user_id)
    return jsonify({'ok': True})


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

    proj = get_project_studies_only(project_id)

    def build_context():
        deep_ctx = yield from _stream_deep_context(user_content, proj, chat)
        manifest = _build_workspace_manifest(proj)
        return "\n\n".join(x for x in (manifest, deep_ctx) if x) or None

    def generate():
        yield from stream_chat_turn(
            scope=SCOPE_PROJECT, chat_id=chat_id, user_id=user_id, model=model,
            system_prompt=PROJECT_CHAT_AGENT_SYSTEM_PROMPT,
            user_content=user_content, report_study_id=report_study_id,
            pin_study_ids=pin_study_ids, project_id=project_id,
            build_context=build_context,
            persist=lambda ac, uip=None: append_chat_messages(
                project_id, user_id, chat_id, user_content, ac, assistant_ui_payload=uip),
        )

    return sse_response(generate)


@app.route('/api/projects/<project_id>/chats/<chat_id>/pinned/<int:study_id>', methods=['POST'])
def api_pin_project_chat_study(project_id, chat_id, study_id):
    chat = get_chat(project_id, g.user_id, chat_id, include_messages=False)
    return pin_toggle(chat, chat_id, SCOPE_PROJECT, study_id, pin=True)


@app.route('/api/projects/<project_id>/chats/<chat_id>/pinned/<int:study_id>', methods=['DELETE'])
def api_unpin_project_chat_study(project_id, chat_id, study_id):
    chat = get_chat(project_id, g.user_id, chat_id, include_messages=False)
    return pin_toggle(chat, chat_id, SCOPE_PROJECT, study_id, pin=False)
