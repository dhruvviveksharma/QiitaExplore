import logging

from flask import g, jsonify, request

from run import app
from config import PROJECT_CHAT_SYSTEM_PROMPT, context_budget_chars
from helpers.agent import stream_agent
from helpers.agent_tool_schemas import PROJECT_TOOL_SCHEMAS
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
    list_pinned_study_meta,
    allowed_project_study_ids,
    move_chat_to_project,
    move_project_chat_to_global,
)
from helpers.llm_helpers import (
    _sse,
    _build_project_study_context,
    llm_chat,
    friendly_llm_error,
)
from helpers.qiita_fetch import _detect_mentioned_study_ids
from helpers.pinned_context import _build_pinned_reports_context
from helpers.pin_flow import stream_pin_flow
from helpers.request_utils import (
    parse_chat_stream_body, load_history_for, sse_response, stream_samples_report,
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

    def generate():
        yield ': keepalive\n\n'
        assistant_parts = []
        ui_payload      = None
        try:
            if pin_study_ids is not None:
                all_pinned = yield from stream_pin_flow(
                    pin_study_ids=pin_study_ids,
                    chat_id=chat_id,
                    scope=SCOPE_PROJECT,
                    full_msgs=full_msgs,
                    model=model,
                    system_prompt=PROJECT_CHAT_SYSTEM_PROMPT,
                    persist=lambda ac: append_chat_messages(project_id, user_id, chat_id, user_content, ac),
                )
                yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_studies": all_pinned,
                                    "pinned_study_meta": list_pinned_study_meta(chat_id, SCOPE_PROJECT)})
                return
            if report_study_id is not None:
                if report_study_id not in member_ids:
                    msg = (
                        f"Study {report_study_id} is not part of this project. "
                        f"Add it via Browse first if you want its report here."
                    )
                    yield _sse("step_start", {"name": "load_samples", "label": f"Loading sample data for study {report_study_id}…"})
                    yield _sse("step_done", {"name": "load_samples", "label": f"Study {report_study_id} is not part of this project"})
                    yield _sse("token", {"token": msg})
                    assistant_parts = [msg]
                else:
                    assistant_parts, ui_payload = yield from stream_samples_report(report_study_id)
            else:
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
                combined_ctx = "\n\n".join(x for x in (study_ctx, deep_ctx) if x) or None

                segments_list = []
                current_text  = []
                for event in stream_agent(
                    full_msgs,
                    system_prompt=PROJECT_CHAT_SYSTEM_PROMPT,
                    model=model,
                    study_context_text=combined_ctx,
                    scope=SCOPE_PROJECT,
                    chat_id=chat_id,
                    tools=PROJECT_TOOL_SCHEMAS,
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
            append_chat_messages(project_id, user_id, chat_id, user_content, assistant_content, assistant_ui_payload=ui_payload)
            if ui_payload and ui_payload.get("kind") == "agent_segments":
                meta = list_pinned_study_meta(chat_id, SCOPE_PROJECT)
                yield _sse("done", {
                    "chat_id": chat_id, "persisted": True,
                    "pinned_studies": [m["study_id"] for m in meta],
                    "pinned_study_meta": meta,
                })
            else:
                yield _sse("done", {"chat_id": chat_id, "persisted": True})
        except Exception as e:
            logger.exception("stream error in project chat %s", chat_id)
            yield _sse("error", {"error": friendly_llm_error(e, model)})

    return sse_response(generate)


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
