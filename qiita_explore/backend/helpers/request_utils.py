"""Shared request-parsing and SSE-response helpers for the chat stream routes."""

from flask import Response, jsonify, stream_with_context

from helpers.llm_helpers import _sse
from helpers.qiita_fetch import _build_samples_report_payload


def parse_chat_stream_body(data):
    """Parse the common fields of a chat-stream POST body.

    Returns (user_content, model, report_study_id, pin_study_ids, err_response).
    `err_response` is a Flask (json, status) tuple if validation failed — the
    caller should return it immediately and ignore the other values.
    """
    user_content    = (data.get('message') or data.get('content') or '').strip()
    model           = data.get('model')
    report_study_id = data.get("report_study_id")
    pin_study_ids   = data.get("pin_study_ids")
    if report_study_id is not None:
        try:
            report_study_id = int(report_study_id)
        except (TypeError, ValueError):
            return None, None, None, None, (jsonify({'error': 'report_study_id must be an integer'}), 400)
    if pin_study_ids is not None:
        try:
            pin_study_ids = [int(x) for x in pin_study_ids]
        except (TypeError, ValueError):
            return None, None, None, None, (jsonify({'error': 'pin_study_ids must be a list of integers'}), 400)
    if not user_content:
        return None, None, None, None, (jsonify({'error': 'message required'}), 400)
    return user_content, model, report_study_id, pin_study_ids, None


def build_full_msgs(messages, user_content):
    full_msgs = [{"role": m.get("role"), "content": m.get("content")} for m in (messages or [])]
    full_msgs.append({"role": "user", "content": user_content})
    return full_msgs


def load_history_for(chat_id: str, scope: str, user_content: str):
    """Read a chat's history and build the LLM message list, in one call.

    Called from the three branches that genuinely consume it — /pin's
    stream_pin_flow, and the two legacy runtimes — rather than once at the top of
    each streaming route. It used to run unconditionally before the runtime
    branch, so a pi-backed turn loaded and JSON-decoded the entire transcript
    (12.2 MB for the largest chat on barnacle, because ui_payload rides along)
    to build a value pi never receives: pi owns history and is sent only the new
    user message.

    Uses the role/content-only projections, so even the legacy paths stop paying
    for ui_payload they never look at.
    """
    from store import load_global_chat_history, load_project_chat_history
    from store.cache import SCOPE_GLOBAL

    loader = load_global_chat_history if scope == SCOPE_GLOBAL else load_project_chat_history
    return build_full_msgs(loader(chat_id), user_content)


def sse_response(generate):
    """Wrap a chat-stream generator in the standard text/event-stream Response."""
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def stream_samples_report(report_study_id):
    """Load a study's full sample report, yielding SSE step/ui events.

    Returns (assistant_parts, ui_payload) via the generator's return value —
    call as `assistant_parts, ui_payload = yield from stream_samples_report(id)`.
    """
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
    return assistant_parts, ui_payload
