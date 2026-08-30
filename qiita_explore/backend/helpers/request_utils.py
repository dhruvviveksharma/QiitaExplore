"""Shared request-parsing and SSE-response helpers for the chat stream routes."""

from flask import Response, jsonify, stream_with_context

from helpers.llm_helpers import _sse
from helpers.qiita_fetch import _build_samples_report_payload, _pin_studies_validated
from store import (
    PINNED_STUDIES_PER_CHAT_CAP,
    list_pinned_study_meta,
    unpin_study_from_chat,
    load_project_chat_history,
    load_global_chat_history,
    SCOPE_PROJECT,
    SCOPE_GLOBAL,
)


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


def load_history_for(chat_id: str, scope: str, user_content: str) -> list:
    """History for a chat turn via the lean role/content-only loaders — never
    decodes ui_payload blobs the LLM can't use anyway."""
    history = (load_global_chat_history(chat_id) if scope == SCOPE_GLOBAL
               else load_project_chat_history(chat_id))
    return build_full_msgs(history, user_content)


def pinned_payload(chat_id: str, scope: str) -> dict:
    """The pinned-studies envelope every pin/unpin response carries.

    One read of chat_pinned_studies; the id list is derived, not re-queried.
    """
    meta = list_pinned_study_meta(chat_id, scope)
    return {
        'pinned_studies': [m["study_id"] for m in meta],
        'pinned_study_meta': meta,
    }


def unpin_response(chat_id: str, scope: str, study_id: int):
    unpin_study_from_chat(chat_id, scope, study_id)
    return jsonify({'ok': True, **pinned_payload(chat_id, scope)})


def pin_response(chat_id: str, scope: str, study_id: int):
    """Pin one study; answer 409 with a reason when it doesn't land.

    The `200 {'ok': True}` this replaced was returned even when the study was
    silently rejected, so a failed pin was indistinguishable from a successful
    one until the chip disappeared on the next reload.
    """
    _, invalid, rejected, all_pinned = _pin_studies_validated(chat_id, scope, [study_id])
    body = {'ok': study_id in all_pinned, 'invalid': invalid, 'rejected': rejected,
            **pinned_payload(chat_id, scope)}
    if body['ok']:
        return jsonify(body)
    if study_id in invalid and scope == SCOPE_PROJECT:
        body['error'] = (
            f"Study {study_id} is not part of this project, is private, not found, "
            f"or has no accessible data"
        )
    elif study_id in invalid:
        body['error'] = (
            f"Study {study_id} is private, not found, or has no accessible data"
        )
    else:
        body['error'] = f"Pin limit reached ({PINNED_STUDIES_PER_CHAT_CAP} studies per chat)"
    return jsonify(body), 409


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
