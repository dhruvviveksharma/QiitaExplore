from flask import g, jsonify, request

from run import app
from config import GLOBAL_CHAT_SYSTEM_PROMPT, context_budget_chars
from helpers.chat_turn import stream_chat_turn
from helpers.pi_client import delete_session as pi_delete_session
from helpers.pin_flow import pin_toggle
from store import (
    SCOPE_GLOBAL,
    append_global_chat_messages,
    create_global_chat,
    delete_global_chat,
    get_global_chat,
    list_global_chats,
)
from helpers.llm_helpers import _sse, _resolve_model, merge_global_chat_context
from helpers.qiita_fetch import _build_pinned_reports_context
from helpers.request_utils import parse_chat_stream_body, sse_response


@app.route('/api/global-chats', methods=['GET'])
def api_list_global_chats():
    return jsonify({'chats': list_global_chats(g.user_id)})


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


@app.route('/api/global-chats/<chat_id>', methods=['DELETE'])
def api_delete_global_chat(chat_id):
    delete_global_chat(g.user_id, chat_id)
    # Best-effort: delete_session() no-ops when pi isn't configured at all.
    pi_delete_session(scope=SCOPE_GLOBAL, chat_id=chat_id, user_id=g.user_id)
    return jsonify({'ok': True})


@app.route('/api/global-chats/<chat_id>/message/stream', methods=['POST'])
def api_global_chat_message_stream(chat_id):
    data             = request.get_json() or {}
    user_id          = g.user_id
    deep_search      = bool(data.get("deep_search"))
    selected_studies = data.get("selected_studies") or []
    user_content, model, report_study_id, pin_study_ids, err_response = parse_chat_stream_body(data)
    if err_response is not None:
        return err_response
    # Normalized once, here, rather than leaving each downstream path to fall
    # back on an invalid/missing model independently.
    model = _resolve_model(model)

    chat = get_global_chat(user_id, chat_id, include_messages=False)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404

    def build_context():
        # Build pinned context once — reused across all paths
        pinned_studies = chat.get("pinned_studies") or []
        pinned_ctx     = None
        if pinned_studies:
            yield _sse("step_start", {"name": "pinned_reports", "label": "Loading pinned study data…"})
            pinned_ctx = _build_pinned_reports_context(pinned_studies)
            yield _sse("step_done", {"name": "pinned_reports", "label": "Pinned reports ready", "detail": f"{len(pinned_studies)} studies"})
            yield ': keepalive\n\n'

        # pinned/selected-study context rides in per-turn via context_block
        # (context.ts hook, never persisted to the pi session) rather than
        # baked into the system prompt.
        budget  = context_budget_chars(model)
        sel_ctx = merge_global_chat_context(selected_studies, user_content, budget) if selected_studies else None
        return "\n\n".join(x for x in (sel_ctx, pinned_ctx) if x) or None

    def generate():
        yield from stream_chat_turn(
            scope=SCOPE_GLOBAL, chat_id=chat_id, user_id=user_id, model=model,
            system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT, pin_system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT,
            user_content=user_content, report_study_id=report_study_id,
            pin_study_ids=pin_study_ids, deep_search=deep_search,
            build_context=build_context,
            persist=lambda ac, uip=None: append_global_chat_messages(
                user_id, chat_id, user_content, ac, assistant_ui_payload=uip),
        )

    return sse_response(generate)


@app.route('/api/global-chats/<chat_id>/pinned/<int:study_id>', methods=['POST'])
def api_pin_global_chat_study(chat_id, study_id):
    chat = get_global_chat(g.user_id, chat_id, include_messages=False)
    return pin_toggle(chat, chat_id, SCOPE_GLOBAL, study_id, pin=True)


@app.route('/api/global-chats/<chat_id>/pinned/<int:study_id>', methods=['DELETE'])
def api_unpin_global_chat_study(chat_id, study_id):
    chat = get_global_chat(g.user_id, chat_id, include_messages=False)
    return pin_toggle(chat, chat_id, SCOPE_GLOBAL, study_id, pin=False)
