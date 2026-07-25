import logging

from flask import g, jsonify, request

from run import app
import config
from config import GLOBAL_CHAT_SYSTEM_PROMPT, context_budget_chars, model_supports_tools
from services.study_service import search_studies_with_sql, build_where_from_plan
from helpers.agent import stream_agent
from helpers.pi_client import stream_chat as pi_stream_chat, PiSidecarError
from helpers.pi_translate import translate as pi_translate, build_segments as pi_build_segments
from helpers.scope_token import mint_scope_token
from store import (
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
    _build_global_search_context,
    merge_global_chat_context,
    llm_chat_stream,
    llm_plan_query,
    friendly_llm_error,
)
from helpers.qiita_fetch import (
    _build_pinned_reports_context,
    _pin_studies_validated,
)
from helpers.pin_flow import stream_pin_flow
from helpers.request_utils import parse_chat_stream_body, build_full_msgs, sse_response, stream_samples_report

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
    if config.PI_BACKEND_GLOBAL:
        # Best-effort: dispose the pi session + its JSONL file too, so a
        # deleted chat doesn't leave an orphaned session on disk. Gated on
        # the flag — otherwise every delete pays a network round trip (or a
        # timeout) to a sidecar that may not even be running.
        from helpers.pi_client import delete_session as pi_delete_session
        pi_delete_session(scope=SCOPE_GLOBAL, chat_id=chat_id)
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
                yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_studies": all_pinned})
                return
            if report_study_id is not None:
                assistant_parts, ui_payload = yield from stream_samples_report(report_study_id)
            else:
                budget    = context_budget_chars(model)
                n_sel     = len(selected_studies) if selected_studies else 0

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
                    raw_events = []

                    def _tee(events):
                        for e in events:
                            raw_events.append(e)
                            yield e

                    for sse_name, payload in pi_translate(_tee(pi_stream_chat(
                        scope=SCOPE_GLOBAL, chat_id=chat_id, model=model,
                        system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT,
                        message=user_content, context_block=combined_ctx,
                        tool_token=tool_token, deep_search=deep_search,
                    ))):
                        if sse_name == "token":
                            assistant_parts.append(payload["token"])
                        yield _sse(sse_name, payload)

                    segments_list = pi_build_segments(raw_events)
                    if segments_list:
                        ui_payload = {"kind": "agent_segments", "segments": segments_list}
                elif model_supports_tools(model):
                    # Legacy Python agentic path: model_supports_tools(model) is
                    # true but PI_BACKEND_GLOBAL is off — flip the flag to cut
                    # over without a deploy if pi-path parity issues turn up.
                    sel_ctx = merge_global_chat_context(selected_studies, [], user_content, budget) if selected_studies else None
                    combined_ctx = "\n\n".join(x for x in (sel_ctx, pinned_ctx) if x) or None
                    # Accumulate segments for persistence
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
                else:
                    # Legacy path: llm_plan_query → keyword search → llm_chat_stream
                    yield _sse("step_start", {"name": "translate_query", "label": "Planning query…"})
                    plan = llm_plan_query(full_msgs)
                    where, search_params = build_where_from_plan(plan)
                    kws = plan.get("keywords", [])
                    display_where = " OR ".join(
                        f"(title/abstract/alias ILIKE '%{kw}%')" for kw in kws
                    ) if kws else "all public studies"
                    yield _sse("step_done", {"name": "translate_query", "label": "Query planned", "detail": plan["description"]})
                    yield _sse("query_plan", {
                        "description": plan["description"],
                        "keywords": kws,
                        "match_mode": "OR",
                        "sql_where": display_where,
                    })
                    yield ': keepalive\n\n'
                    skip_search = bool(plan.get("skip_search"))

                    if skip_search:
                        yield _sse("step_done", {"name": "search_db", "label": "Filtering from conversation context", "detail": "no new search"})
                        if selected_studies:
                            sel_ctx = merge_global_chat_context(selected_studies, [], user_content, budget)
                            combined_ctx = "\n\n".join(x for x in (sel_ctx, pinned_ctx) if x) or None
                        else:
                            combined_ctx = pinned_ctx
                        yield _sse("step_start", {"name": "llm_generate", "label": "Generating response…"})
                        for token in llm_chat_stream(full_msgs, study_context_text=combined_ctx, system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT, model=model):
                            assistant_parts.append(token)
                            yield _sse("token", {"token": token})
                    else:
                        PAGE_SIZE = 50
                        page_num  = max(0, int(plan.get("page") or 0))
                        offset    = page_num * PAGE_SIZE
                        s_label   = "Searching Qiita database…" if page_num == 0 else f"Fetching batch {page_num + 1}…"
                        yield _sse("step_start", {"name": "search_db", "label": s_label})
                        try:
                            studies = search_studies_with_sql(where, search_params, limit=PAGE_SIZE, offset=offset)
                        except Exception:
                            studies = []
                        s_detail = f"{len(studies)} studies found"
                        if n_sel:
                            s_detail += f" · merged with {n_sel} context {'studies' if n_sel != 1 else 'study'}"
                        yield _sse("step_done", {"name": "search_db", "label": "Search complete", "detail": s_detail})
                        yield ': keepalive\n\n'

                        yield _sse("step_start", {"name": "build_context", "label": "Building context…"})
                        if selected_studies:
                            study_ctx = merge_global_chat_context(selected_studies, studies, user_content, budget)
                            yield _sse("step_done", {"name": "build_context", "label": "Context ready", "detail": f"{n_sel} selected + {len(studies)} from search"})
                        else:
                            study_ctx = _build_global_search_context(studies, user_content, budget)
                            yield _sse("step_done", {"name": "build_context", "label": "Context ready", "detail": f"{len(studies)} studies"})
                        yield ': keepalive\n\n'

                        combined_ctx = "\n\n".join(x for x in (study_ctx, pinned_ctx) if x) or None
                        yield _sse("step_start", {"name": "llm_generate", "label": "Generating response…"})
                        for token in llm_chat_stream(full_msgs, study_context_text=combined_ctx, system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT, model=model):
                            assistant_parts.append(token)
                            yield _sse("token", {"token": token})
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
