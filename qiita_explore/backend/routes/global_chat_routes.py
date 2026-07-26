import logging

from flask import g, jsonify, request

from run import app
import config
from config import GLOBAL_CHAT_SYSTEM_PROMPT, context_budget_chars, model_supports_tools
from helpers.agent import stream_agent
from helpers.pi_client import delete_session as pi_delete_session
from helpers.pi_turn import stream_pi_turn
from helpers.scope_token import mint_scope_token
from store import (
    get_pi_session_file,
    SCOPE_GLOBAL,
    append_global_chat_messages,
    create_global_chat,
    delete_global_chat,
    get_global_chat,
    list_global_chats,
    list_pinned_studies,
    unpin_study_from_chat,
)
from helpers.llm_helpers import (
    _sse,
    _resolve_model,
    merge_global_chat_context,
    friendly_llm_error,
)
from helpers.qiita_fetch import (
    _build_pinned_reports_context,
    _pin_studies_validated,
)
from helpers.pin_flow import stream_pin_flow
from helpers.request_utils import parse_chat_stream_body, load_history_for, sse_response, stream_samples_report

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


@app.route('/api/global-chats/<chat_id>', methods=['DELETE'])
def api_delete_global_chat(chat_id):
    delete_global_chat(g.user_id, chat_id)
    # Best-effort, and deliberately not gated on PI_BACKEND_GLOBAL: a chat
    # created while the flag was on still has a pi session after it is flipped
    # off. delete_session() no-ops when pi isn't configured at all.
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
    # Normalized once, here, rather than leaving each downstream path (pi,
    # stream_agent, llm_chat_stream) to fall back on an invalid/missing model
    # independently — makes model_supports_tools(model) below a reliable
    # check against a real ALLOWED_MODELS member instead of an unvalidated
    # client-supplied string.
    model = _resolve_model(model)

    chat = get_global_chat(user_id, chat_id, include_messages=False)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404

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
                    full_msgs=load_history_for(chat_id, SCOPE_GLOBAL, user_content),
                    model=model,
                    system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT,
                    persist=lambda ac: append_global_chat_messages(user_id, chat_id, user_content, ac),
                )
                yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_studies": all_pinned})
                return
            if report_study_id is not None:
                assistant_parts, ui_payload = yield from stream_samples_report(report_study_id)
            else:
                budget    = context_budget_chars(model)

                # Build pinned context once — reused across all paths
                pinned_studies = chat.get("pinned_studies") or []
                pinned_ctx     = None
                if pinned_studies:
                    yield _sse("step_start", {"name": "pinned_reports", "label": "Loading pinned study data…"})
                    pinned_ctx = _build_pinned_reports_context(pinned_studies)
                    yield _sse("step_done", {"name": "pinned_reports", "label": "Pinned reports ready", "detail": f"{len(pinned_studies)} studies"})
                    yield ': keepalive\n\n'

                if model_supports_tools(model) and config.PI_BACKEND_GLOBAL:
                    # pi-backed agentic path: pi owns history/compaction/the tool
                    # loop. Only the new user message is sent — no full_msgs — and
                    # pinned/selected-study context rides in per-turn via
                    # context_block (context.ts hook, never persisted to the pi
                    # session) rather than baked into the system prompt.
                    sel_ctx = merge_global_chat_context(selected_studies, [], user_content, budget) if selected_studies else None
                    combined_ctx = "\n\n".join(x for x in (sel_ctx, pinned_ctx) if x) or None
                    tool_token = mint_scope_token(
                        user_id=user_id, scope=SCOPE_GLOBAL, chat_id=chat_id,
                        deep_search=deep_search, ttl_seconds=config.PI_SCOPE_TOKEN_TTL_SECONDS,
                    )
                    # deep_search is NOT passed here — it already rode into
                    # tool_token above (mint_scope_token), which is where
                    # internal_tool_routes.py actually reads it. stream_chat
                    # (helpers/pi_client.py) no longer accepts it as a
                    # separate parameter.
                    assistant_parts, ui_payload = yield from stream_pi_turn(
                        scope=SCOPE_GLOBAL, chat_id=chat_id, user_id=user_id, model=model,
                        session_file=get_pi_session_file(chat_id, SCOPE_GLOBAL),
                        system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT,
                        message=user_content, context_block=combined_ctx,
                        tool_token=tool_token,
                    )
                else:
                    # Legacy Python agentic path — used when PI_BACKEND_GLOBAL
                    # is off. Flip the flag to cut over without a deploy if
                    # pi-path parity issues turn up.
                    #
                    # This used to be `elif model_supports_tools(model):`, with
                    # a third `else` running llm_plan_query -> keyword search
                    # for models that couldn't call tools. That branch is
                    # deleted: model_supports_tools(model) is True for every
                    # entry in MODEL_METADATA now (gemma-small was the last
                    # False, corrected once its real NRP listing showed it
                    # does support tool calls), so it had no live caller. A
                    # future supports_tools=False model would reach
                    # stream_agent here instead — contained by this
                    # generator's own try/except at the bottom of this
                    # function (a per-request error, not a crash), which is
                    # an acceptable failure mode for a case nothing in
                    # ALLOWED_MODELS currently produces.
                    sel_ctx = merge_global_chat_context(selected_studies, [], user_content, budget) if selected_studies else None
                    combined_ctx = "\n\n".join(x for x in (sel_ctx, pinned_ctx) if x) or None
                    # Accumulate segments for persistence
                    segments_list = []
                    current_text  = []
                    for event in stream_agent(
                        load_history_for(chat_id, SCOPE_GLOBAL, user_content),
                        system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT,
                        model=model,
                        study_context_text=combined_ctx,
                        scope=SCOPE_GLOBAL,
                        chat_id=chat_id,
                        deep_search=deep_search,
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
            # For agent turns, send the full current pinned list so the frontend can sync
            if ui_payload and ui_payload.get("kind") == "agent_segments":
                final_pinned = list_pinned_studies(chat_id, SCOPE_GLOBAL)
                yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_studies": final_pinned})
            else:
                yield _sse("done", {"chat_id": chat_id, "persisted": True})
        except Exception as e:
            logger.exception("stream error in global chat %s", chat_id)
            yield _sse("error", {"error": friendly_llm_error(e, model)})

    return sse_response(generate)


@app.route('/api/global-chats/<chat_id>/pinned/<int:study_id>', methods=['POST'])
def api_pin_global_chat_study(chat_id, study_id):
    chat = get_global_chat(g.user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    _, _, _, all_pinned = _pin_studies_validated(chat_id, SCOPE_GLOBAL, [study_id])
    return jsonify({'ok': True, 'pinned_studies': all_pinned})


@app.route('/api/global-chats/<chat_id>/pinned/<int:study_id>', methods=['DELETE'])
def api_unpin_global_chat_study(chat_id, study_id):
    chat = get_global_chat(g.user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    unpin_study_from_chat(chat_id, SCOPE_GLOBAL, study_id)
    return jsonify({'ok': True, 'pinned_studies': list_pinned_studies(chat_id, SCOPE_GLOBAL)})
