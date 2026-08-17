import logging

from flask import g, jsonify, request

from run import app
from config import GLOBAL_CHAT_SYSTEM_PROMPT
from helpers.agent import stream_agent
from helpers.agent_tool_schemas import TOOL_SCHEMAS
from store import (
    SCOPE_GLOBAL,
    append_global_chat_messages,
    create_global_chat,
    delete_global_chat,
    update_global_chat_title,
    get_global_chat,
    global_chat_exists,
    list_global_chats,
    list_pinned_studies,
    list_pinned_study_meta,
)
from helpers.llm_helpers import (
    _sse,
    friendly_llm_error,
)
from helpers.pinned_context import _build_pinned_reports_context
from helpers.pin_flow import stream_pin_flow
from helpers.request_utils import (
    parse_chat_stream_body, build_full_msgs, sse_response, stream_samples_report,
    pin_response, unpin_response,
)

logger = logging.getLogger(__name__)


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


@app.route('/api/global-chats/<chat_id>', methods=['PATCH'])
def api_rename_global_chat(chat_id):
    data = request.get_json() or {}
    title = data.get('title')
    if not isinstance(title, str) or not title.strip():
        return jsonify({'error': 'title is required'}), 400
    chat = update_global_chat_title(g.user_id, chat_id, title)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    return jsonify(chat)


@app.route('/api/global-chats/<chat_id>', methods=['DELETE'])
def api_delete_global_chat(chat_id):
    delete_global_chat(g.user_id, chat_id)
    return jsonify({'ok': True})


@app.route('/api/global-chats/<chat_id>/message/stream', methods=['POST'])
def api_global_chat_message_stream(chat_id):
    data        = request.get_json() or {}
    user_id     = g.user_id
    deep_search = bool(data.get("deep_search"))
    user_content, model, report_study_id, pin_study_ids, err_response = parse_chat_stream_body(data)
    if err_response is not None:
        return err_response

    chat = get_global_chat(user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404

    full_msgs = build_full_msgs(chat.get('messages'), user_content)

    def generate():
        assistant_parts = []
        ui_payload      = None
        try:
            yield ': keepalive\n\n'
            if pin_study_ids is not None:
                all_pinned = yield from stream_pin_flow(
                    pin_study_ids=pin_study_ids,
                    chat_id=chat_id,
                    scope=SCOPE_GLOBAL,
                    full_msgs=full_msgs,
                    model=model,
                    system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT,
                    persist=lambda ac: append_global_chat_messages(user_id, chat_id, user_content, ac),
                )
                yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_studies": all_pinned,
                                    "pinned_study_meta": list_pinned_study_meta(chat_id, SCOPE_GLOBAL)})
                return
            if report_study_id is not None:
                assistant_parts, ui_payload = yield from stream_samples_report(report_study_id)
            else:
                pinned_studies = chat.get("pinned_studies") or []
                pinned_ctx     = None
                if pinned_studies:
                    yield _sse("step_start", {"name": "pinned_reports", "label": "Loading pinned study data…"})
                    pinned_ctx = _build_pinned_reports_context(
                        pinned_studies, model, tools_available=True)
                    yield _sse("step_done", {"name": "pinned_reports", "label": "Pinned reports ready", "detail": f"{len(pinned_studies)} studies"})
                    yield ': keepalive\n\n'

                combined_ctx = pinned_ctx
                segments_list = []
                current_text  = []
                for event in stream_agent(
                    full_msgs,
                    system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT,
                    model=model,
                    study_context_text=combined_ctx,
                    scope=SCOPE_GLOBAL,
                    chat_id=chat_id,
                    deep_search=deep_search,
                    tools=TOOL_SCHEMAS,
                ):
                    etype = event["type"]
                    if etype == "agent_start":
                        yield _sse("agent_start", {})
                    elif etype == "token":
                        current_text.append(event["token"])
                        assistant_parts.append(event["token"])
                        yield _sse("token", {"token": event["token"]})
                    elif etype == "segment_tool_call":
                        if current_text:
                            segments_list.append({"type": "text", "content": "".join(current_text), "done": True})
                            current_text = []
                        segments_list.append({"type": "tool", "name": event["name"],
                                               "label": event["label"], "args": event["args"],
                                               "done": False, "result": None})
                        yield _sse("segment_tool_call", {"name": event["name"], "label": event["label"], "args": event["args"]})
                    elif etype == "segment_tool_result":
                        for seg in segments_list:
                            if seg.get("type") == "tool" and seg.get("name") == event["name"] and not seg.get("done"):
                                seg["done"] = True
                                seg["result"] = {"label": event["label"], "detail": event["detail"], "ui_payload": event.get("ui_payload")}
                                break
                        yield _sse("segment_tool_result", {"name": event["name"], "label": event["label"],
                                                            "detail": event.get("detail", ""), "ui_payload": event.get("ui_payload")})
                if current_text:
                    segments_list.append({"type": "text", "content": "".join(current_text), "done": True})
                if segments_list:
                    ui_payload = {"kind": "agent_segments", "segments": segments_list}
            assistant_content = "".join(assistant_parts).strip()
            append_global_chat_messages(user_id, chat_id, user_content, assistant_content, assistant_ui_payload=ui_payload)
            if ui_payload and ui_payload.get("kind") == "agent_segments":
                final_pinned = list_pinned_studies(chat_id, SCOPE_GLOBAL)
                yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_studies": final_pinned,
                                    "pinned_study_meta": list_pinned_study_meta(chat_id, SCOPE_GLOBAL)})
            else:
                yield _sse("done", {"chat_id": chat_id, "persisted": True})
        except Exception as e:
            logger.exception("stream error in global chat %s", chat_id)
            yield _sse("error", {"error": friendly_llm_error(e, model)})

    return sse_response(generate)


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
