"""Shared pin-and-acknowledge SSE flow for project and global chat streams."""

from flask import jsonify

from helpers.llm_helpers import _sse, llm_chat_stream
from helpers.qiita_fetch import _build_pinned_reports_context, _pin_studies_validated
from store import list_pinned_studies, unpin_study_from_chat


def stream_pin_flow(pin_study_ids, chat_id, scope, full_msgs, model, persist, system_prompt=None):
    """Validate + pin studies, generate an LLM ack, persist it, and yield SSE events.

    `persist(assistant_content)` is called once the ack has streamed fully; the
    caller supplies it pre-bound with whatever project/global identifiers its
    own append-message function needs. Returns `all_pinned` via `yield from` so
    the caller can emit its own final `done` event.
    """
    yield _sse("step_start", {"name": "pin_studies", "label": f"Pinning {len(pin_study_ids)} {'study' if len(pin_study_ids) == 1 else 'studies'}…"})
    pinned_now, invalid, rejected, all_pinned = _pin_studies_validated(chat_id, scope, pin_study_ids)
    detail_parts = []
    if pinned_now:
        detail_parts.append(f"{len(pinned_now)} pinned")
    if invalid:
        detail_parts.append(f"{len(invalid)} not found")
    if rejected:
        detail_parts.append(f"{len(rejected)} over cap")
    yield _sse("step_done", {"name": "pin_studies", "label": "Studies pinned", "detail": " · ".join(detail_parts) or "none added"})

    deep_ctx = None
    if all_pinned:
        yield _sse("step_start", {"name": "deep_context", "label": "Loading pinned study data…"})
        deep_ctx = _build_pinned_reports_context(all_pinned)
        yield _sse("step_done", {"name": "deep_context", "label": "Pinned reports ready", "detail": f"{len(all_pinned)} studies"})
        yield ': keepalive\n\n'

    ack_lines = []
    if pinned_now:
        ack_lines.append(f"Newly pinned: {', '.join(str(s) for s in pinned_now)}.")
    if invalid:
        ack_lines.append(f"Not found / private (skipped): {', '.join(str(s) for s in invalid)}.")
    if rejected:
        ack_lines.append(f"Could not pin (10-study cap reached): {', '.join(str(s) for s in rejected)}.")
    ack_instruction = (
        " ".join(ack_lines) +
        " Using the STUDY CONTEXT provided, give a one-sentence summary of each newly-pinned study "
        "(data type, sample count, topic). Be concise."
    )
    llm_msgs = full_msgs[:-1] + [{"role": "user", "content": ack_instruction}]

    yield _sse("step_start", {"name": "llm_generate", "label": "Generating response…"})
    assistant_parts = []
    for token in llm_chat_stream(llm_msgs, study_context_text=deep_ctx, system_prompt=system_prompt, model=model):
        assistant_parts.append(token)
        yield _sse("token", {"token": token})
    assistant_content = "".join(assistant_parts).strip()
    persist(assistant_content)

    return all_pinned


def pin_toggle(chat, chat_id, scope, study_id, *, pin):
    """Shared body of the four pin/unpin REST endpoints — they differ only in
    the getter used to fetch `chat` (project chat's needs project_id too) and
    the scope constant, both resolved by the caller before this is called."""
    if not chat:
        return jsonify({'error': 'Chat not found'}), 404
    if pin:
        _, _, _, all_pinned = _pin_studies_validated(chat_id, scope, [study_id])
    else:
        unpin_study_from_chat(chat_id, scope, study_id)
        all_pinned = list_pinned_studies(chat_id, scope)
    return jsonify({'ok': True, 'pinned_studies': all_pinned})
