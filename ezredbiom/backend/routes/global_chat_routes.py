import logging

from flask import Response, jsonify, request, stream_with_context

from run import app
from config import GLOBAL_CHAT_SYSTEM_PROMPT
from services.study_service import search_studies_with_sql, search_studies_from_plan
from store import (
    SCOPE_GLOBAL,
    append_global_chat_messages,
    create_global_chat,
    delete_global_chat,
    get_global_chat,
    list_global_chats,
    list_pinned_studies,
    pin_study_to_chat,
    unpin_study_from_chat,
)
from helpers.llm_helpers import (
    _sse,
    _build_global_search_context,
    merge_global_chat_context,
    llm_chat_stream,
    llm_plan_query,
    friendly_llm_error,
)
from helpers.qiita_fetch import (
    _build_pinned_reports_context,
    _build_samples_report_payload,
)

logger = logging.getLogger(__name__)


@app.route('/api/global-chats', methods=['GET'])
def api_list_global_chats():
    user_id = request.args.get('user_id') or 'default'
    return jsonify({'chats': list_global_chats(user_id)})


@app.route('/api/global-chats', methods=['POST'])
def api_create_global_chat():
    data    = request.get_json() or {}
    user_id = (data.get('user_id') or 'default').strip() or 'default'
    title   = data.get('title')
    chat    = create_global_chat(user_id, title=title)
    if not chat:
        return jsonify({'error': 'Failed to create global chat'}), 500
    return jsonify(chat)


@app.route('/api/global-chats/<chat_id>', methods=['GET'])
def api_get_global_chat(chat_id):
    user_id = request.args.get('user_id') or 'default'
    chat    = get_global_chat(user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    return jsonify(chat)


@app.route('/api/global-chats/<chat_id>', methods=['DELETE'])
def api_delete_global_chat(chat_id):
    user_id = request.args.get('user_id') or 'default'
    delete_global_chat(user_id, chat_id)
    return jsonify({'ok': True})


@app.route('/api/global-chats/<chat_id>/message/stream', methods=['POST'])
def api_global_chat_message_stream(chat_id):
    data             = request.get_json() or {}
    user_id          = (data.get('user_id') or 'default').strip() or 'default'
    user_content     = (data.get('message') or data.get('content') or '').strip()
    model            = data.get('model')
    report_study_id  = data.get("report_study_id")
    selected_studies = data.get("selected_studies") or []
    if report_study_id is not None:
        try:
            report_study_id = int(report_study_id)
        except (TypeError, ValueError):
            return jsonify({'error': 'report_study_id must be an integer'}), 400
    if not user_content:
        return jsonify({'error': 'message required'}), 400

    chat = get_global_chat(user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404

    messages  = chat.get('messages') or []
    full_msgs = [{"role": m.get("role"), "content": m.get("content")} for m in messages]
    full_msgs.append({"role": "user", "content": user_content})

    def generate():
        assistant_parts = []
        ui_payload      = None
        try:
            yield ': keepalive\n\n'
            if report_study_id is not None:
                yield _sse("step_start", {"name": "load_samples", "label": f"Loading sample data for study {report_study_id}…"})
                try:
                    ui_payload  = _build_samples_report_payload(report_study_id)
                    num_samples = (ui_payload.get("header") or {}).get("num_samples") or len(ui_payload.get("samples") or [])
                    assistant_parts = [f"Loaded full sample metadata for study {report_study_id} ({num_samples} samples). See inline browser."]
                    yield _sse("step_done", {"name": "load_samples", "label": "Sample data loaded", "detail": f"{num_samples} samples"})
                    yield _sse("ui", ui_payload)
                except ValueError:
                    assistant_parts = [f"Study {report_study_id} is private or has no accessible sample data in Qiita."]
                    yield _sse("step_done", {"name": "load_samples", "label": f"Study {report_study_id} is private — no accessible data"})
                    ui_payload = None
            else:
                yield _sse("step_start", {"name": "translate_query", "label": "Planning query…"})
                plan = llm_plan_query(full_msgs)
                yield _sse("step_done", {"name": "translate_query", "label": "Query planned", "detail": plan["description"]})
                yield _sse("query_plan", {
                    "description": plan["description"],
                    "keywords": plan.get("keywords", []),
                    "match_mode": plan.get("match_mode", "AND"),
                })
                yield ': keepalive\n\n'
                yield _sse("step_start", {"name": "search_db", "label": "Searching Qiita database…"})
                try:
                    studies = search_studies_from_plan(plan)
                except Exception:
                    studies = []
                n_sel = len(selected_studies) if selected_studies else 0
                detail = f"{len(studies)} studies found"
                if n_sel:
                    detail += f" · merged with {n_sel} context {'studies' if n_sel != 1 else 'study'}"
                yield _sse("step_done", {"name": "search_db", "label": "Search complete", "detail": detail})
                yield ': keepalive\n\n'
                yield _sse("step_start", {"name": "build_context", "label": "Building context…"})
                if selected_studies:
                    study_ctx = merge_global_chat_context(selected_studies, studies, user_content)
                    yield _sse("step_done", {"name": "build_context", "label": "Context ready", "detail": f"{n_sel} selected + {len(studies)} from search"})
                else:
                    study_ctx = _build_global_search_context(studies, user_content)
                    yield _sse("step_done", {"name": "build_context", "label": "Context ready", "detail": f"{len(studies)} studies"})
                yield ': keepalive\n\n'
                pinned_studies = chat.get("pinned_studies") or []
                pinned_ctx     = None
                if pinned_studies:
                    yield _sse("step_start", {"name": "pinned_reports", "label": "Loading pinned study data…"})
                    pinned_ctx = _build_pinned_reports_context(pinned_studies)
                    yield _sse("step_done", {"name": "pinned_reports", "label": "Pinned reports ready", "detail": f"{len(pinned_studies)} studies"})
                    yield ': keepalive\n\n'
                combined_ctx = "\n\n".join(x for x in (study_ctx, pinned_ctx) if x) or None
                yield _sse("step_start", {"name": "llm_generate", "label": "Generating response…"})
                for token in llm_chat_stream(
                    full_msgs,
                    study_context_text=combined_ctx,
                    system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT,
                    model=model,
                ):
                    assistant_parts.append(token)
                    yield _sse("token", {"token": token})
            assistant_content = "".join(assistant_parts).strip()
            append_global_chat_messages(user_id, chat_id, user_content, assistant_content, assistant_ui_payload=ui_payload)
            if report_study_id is not None and ui_payload is not None:
                try:
                    pin_study_to_chat(chat_id, SCOPE_GLOBAL, report_study_id)
                except Exception:
                    logger.exception("failed to pin study %s to global chat %s", report_study_id, chat_id)
            yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_study_id": report_study_id if ui_payload else None})
        except Exception as e:
            logger.exception("stream error in global chat %s", chat_id)
            yield _sse("error", {"error": friendly_llm_error(e, model)})

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route('/api/global-chats/<chat_id>/pinned/<int:study_id>', methods=['DELETE'])
def api_unpin_global_chat_study(chat_id, study_id):
    user_id = request.args.get('user_id') or 'default'
    chat    = get_global_chat(user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    unpin_study_from_chat(chat_id, SCOPE_GLOBAL, study_id)
    return jsonify({'ok': True, 'pinned_studies': list_pinned_studies(chat_id, SCOPE_GLOBAL)})
