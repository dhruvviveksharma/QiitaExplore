import logging

from flask import g, jsonify, request

from run import app
import config
from config import context_budget_chars, PROJECT_CHAT_AGENT_SYSTEM_PROMPT
from helpers.pi_client import stream_chat as pi_stream_chat, PiSidecarError
from helpers.pi_translate import translate as pi_translate, build_segments as pi_build_segments
from helpers.scope_token import mint_scope_token
from store import (
    SCOPE_PROJECT,
    append_chat_messages,
    create_chat,
    delete_chat,
    get_chat,
    get_project,
    get_project_studies_only,
    list_pinned_studies,
    unpin_study_from_chat,
)
from helpers.llm_helpers import (
    _sse,
    _build_project_study_context,
    llm_chat,
    llm_chat_stream,
    friendly_llm_error,
)
from helpers.qiita_fetch import (
    _build_pinned_reports_context,
    _detect_mentioned_study_ids,
    _pin_studies_validated,
)
from helpers.pin_flow import stream_pin_flow
from helpers.request_utils import parse_chat_stream_body, build_full_msgs, sse_response, stream_samples_report

logger = logging.getLogger(__name__)


def _build_workspace_manifest(proj) -> str:
    """Short 'what's in this workspace' list — one line per study (id, title,
    data types). Deliberately NOT the full 3-tier _build_project_study_context
    cascade: pi's own compaction plus the workspace-scoped search_studies tool
    (helpers/project_scope.py) replace that. Sent as part of context_block
    (per-turn, never persisted to the pi session) rather than system_prompt,
    since pi's system prompt is fixed for the session's lifetime but workspace
    membership can change between turns."""
    studies = (proj or {}).get("studies") or []
    if not studies:
        return "This project's workspace currently has no studies added."
    lines = [f"This project's workspace contains {len(studies)} studies:"]
    for s in studies:
        title = (s.get("study_title") or "Untitled study").strip()
        dtypes = s.get("data_types") or "unknown"
        lines.append(f"- Study {s.get('study_id')}: {title} ({dtypes})")
    return "\n".join(lines)


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
        if config.PI_BACKEND_PROJECT:
            tool_token = mint_scope_token(
                user_id=user_id, scope=SCOPE_PROJECT, chat_id=chat["chat_id"],
                project_id=project_id, ttl_seconds=config.PI_SCOPE_TOKEN_TTL_SECONDS,
            )
            raw_events = list(pi_stream_chat(
                scope=SCOPE_PROJECT, chat_id=chat["chat_id"], model=model,
                system_prompt=PROJECT_CHAT_AGENT_SYSTEM_PROMPT,
                message=first_message, context_block=_build_workspace_manifest(proj),
                tool_token=tool_token,
            ))
            assistant_content = "".join(
                payload["token"] for name, payload in pi_translate(raw_events) if name == "token"
            ).strip()
            segments_list = pi_build_segments(raw_events)
            ui_payload = {"kind": "agent_segments", "segments": segments_list} if segments_list else None
            append_chat_messages(project_id, user_id, chat["chat_id"], first_message, assistant_content, assistant_ui_payload=ui_payload)
        else:
            study_ctx         = _build_project_study_context(proj, user_id=user_id, budget=context_budget_chars(model))
            assistant_content = llm_chat([{"role": "user", "content": first_message}], study_context_text=study_ctx, model=model)
            append_chat_messages(project_id, user_id, chat["chat_id"], first_message, assistant_content)
    chat = get_chat(project_id, user_id, chat["chat_id"])
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
    if config.PI_BACKEND_PROJECT:
        from helpers.pi_client import delete_session as pi_delete_session
        pi_delete_session(scope=SCOPE_PROJECT, chat_id=chat_id)
    return jsonify({'ok': True})


@app.route('/api/projects/<project_id>/chats/<chat_id>/message/stream', methods=['POST'])
def api_chat_message_stream(project_id, chat_id):
    data    = request.get_json() or {}
    user_id = g.user_id
    user_content, model, report_study_id, pin_study_ids, err_response = parse_chat_stream_body(data)
    if err_response is not None:
        return err_response

    chat = get_chat(project_id, user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404

    proj      = get_project_studies_only(project_id)
    full_msgs = build_full_msgs(chat.get('messages'), user_content)

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
                    persist=lambda ac: append_chat_messages(project_id, user_id, chat_id, user_content, ac),
                )
                yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_studies": all_pinned})
                return
            if report_study_id is not None:
                assistant_parts, ui_payload = yield from stream_samples_report(report_study_id)
            elif config.PI_BACKEND_PROJECT:
                # pi-backed agentic path: project chat gains the same tool loop
                # as global chat, but every tool call is hard-scoped server-side
                # to this project's studies (helpers/project_scope.py) — not a
                # prompt request, an enforced boundary.
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
                    deep_ctx = _build_pinned_reports_context(deep_ids)
                    yield _sse("step_done", {"name": "deep_context", "label": done_label, "detail": f"{len(deep_ids)} studies"})
                    yield ': keepalive\n\n'
                manifest = _build_workspace_manifest(proj)
                combined_ctx = "\n\n".join(x for x in (manifest, deep_ctx) if x) or None

                tool_token = mint_scope_token(
                    user_id=user_id, scope=SCOPE_PROJECT, chat_id=chat_id,
                    project_id=project_id, ttl_seconds=config.PI_SCOPE_TOKEN_TTL_SECONDS,
                )
                raw_events = []

                def _tee(events):
                    for e in events:
                        raw_events.append(e)
                        yield e

                for sse_name, payload in pi_translate(_tee(pi_stream_chat(
                    scope=SCOPE_PROJECT, chat_id=chat_id, model=model,
                    system_prompt=PROJECT_CHAT_AGENT_SYSTEM_PROMPT,
                    message=user_content, context_block=combined_ctx,
                    tool_token=tool_token,
                ))):
                    if sse_name == "token":
                        assistant_parts.append(payload["token"])
                    yield _sse(sse_name, payload)

                segments_list = pi_build_segments(raw_events)
                if segments_list:
                    ui_payload = {"kind": "agent_segments", "segments": segments_list}
            else:
                # Legacy non-agentic path: context-stuffed, no tools. Flip
                # PI_BACKEND_PROJECT to cut over without a deploy if pi-path
                # parity issues turn up after global chat has already shipped.
                num_proj_studies = len((proj or {}).get("studies") or [])
                yield _sse("step_start", {"name": "build_context", "label": "Loading study context…"})
                study_ctx = _build_project_study_context(proj, user_id=user_id, budget=context_budget_chars(model))
                yield _sse("step_done", {"name": "build_context", "label": "Study context ready", "detail": f"{num_proj_studies} studies"})
                yield ': keepalive\n\n'
                # Merge dynamically detected study IDs with any explicitly pinned ones
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
                    deep_ctx = _build_pinned_reports_context(deep_ids)
                    yield _sse("step_done", {"name": "deep_context", "label": done_label, "detail": f"{len(deep_ids)} studies"})
                    yield ': keepalive\n\n'
                combined_ctx = "\n\n".join(x for x in (study_ctx, deep_ctx) if x) or None
                yield _sse("step_start", {"name": "llm_generate", "label": "Generating response…"})
                for token in llm_chat_stream(full_msgs, study_context_text=combined_ctx, model=model):
                    assistant_parts.append(token)
                    yield _sse("token", {"token": token})
            assistant_content = "".join(assistant_parts).strip()
            append_chat_messages(project_id, user_id, chat_id, user_content, assistant_content, assistant_ui_payload=ui_payload)
            if ui_payload and ui_payload.get("kind") == "agent_segments":
                # Project chat can now pin mid-turn too (the pin_study tool) —
                # sync the frontend's pinned state, same as global chat does.
                final_pinned = list_pinned_studies(chat_id, SCOPE_PROJECT)
                yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_studies": final_pinned})
            else:
                yield _sse("done", {"chat_id": chat_id, "persisted": True})
        except Exception as e:
            logger.exception("stream error in project chat %s", chat_id)
            yield _sse("error", {"error": friendly_llm_error(e, model)})

    return sse_response(generate)


@app.route('/api/projects/<project_id>/chats/<chat_id>/pinned/<int:study_id>', methods=['POST'])
def api_pin_project_chat_study(project_id, chat_id, study_id):
    chat = get_chat(project_id, g.user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    _, _, _, all_pinned = _pin_studies_validated(chat_id, SCOPE_PROJECT, [study_id])
    return jsonify({'ok': True, 'pinned_studies': all_pinned})


@app.route('/api/projects/<project_id>/chats/<chat_id>/pinned/<int:study_id>', methods=['DELETE'])
def api_unpin_project_chat_study(project_id, chat_id, study_id):
    chat = get_chat(project_id, g.user_id, chat_id)
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    unpin_study_from_chat(chat_id, SCOPE_PROJECT, study_id)
    return jsonify({'ok': True, 'pinned_studies': list_pinned_studies(chat_id, SCOPE_PROJECT)})
