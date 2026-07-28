"""One chat turn, as SSE — shared by project and global chat.

Both streaming routes ran the same five-branch generator (pin, report,
main-turn, persist, error) differing only in a scope constant, how the turn's
context block is built, and what mint_scope_token needs. Every turn-level
feature (the `pinned_studies` sync on `done`, the `runtime` event) shipped to
one route first and had to be back-fitted to the other. This is the one copy.

Follows the sub-generator convention already used by
helpers/pin_flow.py::stream_pin_flow and helpers/request_utils.py::
stream_samples_report: the caller writes

    yield from stream_chat_turn(...)

and keeps only the part that genuinely differs between the two scopes, which
is `build_context`.
"""

import logging

import config
from helpers.llm_helpers import _sse, friendly_llm_error
from helpers.pi_turn import stream_pi_turn
from helpers.pin_flow import stream_pin_flow
from helpers.request_utils import load_history_for, stream_samples_report
from helpers.scope_token import mint_scope_token
from store import get_pi_session_file, list_pinned_studies

logger = logging.getLogger(__name__)


def stream_chat_turn(*, scope, chat_id, user_id, model, system_prompt,
                      user_content, report_study_id, pin_study_ids,
                      persist, build_context, pin_system_prompt=None,
                      project_id=None, deep_search=False):
    """Yield SSE strings for one chat turn.

    `persist(assistant_content, ui_payload=None)` stores the turn; it is
    called with just `assistant_content` from the pin branch (matching
    stream_pin_flow's own single-arg call) and with both from the main turn.

    `build_context()` is called only for the main turn. Like
    stream_samples_report, it may itself yield SSE step events and returns
    the context_block string (or None) via `yield from`.

    `pin_system_prompt` preserves a pre-existing asymmetry: global chat's pin
    acknowledgement uses GLOBAL_CHAT_SYSTEM_PROMPT, project chat's has never
    passed one (falling back to the generic CHAT_SYSTEM_PROMPT) — carried
    over as-is, not something this merge changes.
    """
    assistant_parts = []
    ui_payload = None
    try:
        yield ': keepalive\n\n'
        if pin_study_ids is not None:
            all_pinned = yield from stream_pin_flow(
                pin_study_ids=pin_study_ids,
                chat_id=chat_id,
                scope=scope,
                full_msgs=load_history_for(chat_id, scope, user_content),
                model=model,
                system_prompt=pin_system_prompt,
                persist=persist,
            )
            yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_studies": all_pinned})
            return
        if report_study_id is not None:
            assistant_parts, ui_payload = yield from stream_samples_report(report_study_id)
        else:
            # Every tool call is hard-scoped server-side (project:
            # helpers/project_scope.py) — not a prompt request, an enforced
            # boundary.
            yield _sse("runtime", {"runtime": "pi"})
            context_block = yield from build_context()
            tool_token = mint_scope_token(
                user_id=user_id, scope=scope, chat_id=chat_id,
                project_id=project_id, deep_search=deep_search,
                ttl_seconds=config.PI_SCOPE_TOKEN_TTL_SECONDS,
            )
            assistant_parts, ui_payload = yield from stream_pi_turn(
                scope=scope, chat_id=chat_id, user_id=user_id, model=model,
                session_file=get_pi_session_file(chat_id, scope),
                system_prompt=system_prompt, message=user_content,
                context_block=context_block, tool_token=tool_token,
            )
        assistant_content = "".join(assistant_parts).strip()
        persist(assistant_content, ui_payload)
        if ui_payload and ui_payload.get("kind") == "agent_segments":
            # For agent turns, send the full current pinned list so the
            # frontend can sync (pin_study can pin mid-turn).
            final_pinned = list_pinned_studies(chat_id, scope)
            yield _sse("done", {"chat_id": chat_id, "persisted": True, "pinned_studies": final_pinned})
        else:
            yield _sse("done", {"chat_id": chat_id, "persisted": True})
    except Exception as e:
        logger.exception("stream error in %s chat %s", scope, chat_id)
        yield _sse("error", {"error": friendly_llm_error(e, model)})
