"""Shared turn lifecycle for the two chat-stream routes.

Both project and global chat run the identical five-branch turn (pin flow /
samples report / agentic main turn / persist / error) — previously two
independently-maintained ~100-line generate() closures, where every turn-level
feature shipped to one route first and had to be back-fitted to the other.
The genuinely scope-specific parts arrive as callables:

  build_context() — generator: yields SSE strings (step events, keepalives)
                    while building, returns the combined context text or None.
  persist(assistant_content, ui_payload=None) — pre-bound with the route's
                    project/global identifiers and the user_content.
  report_guard(study_id) -> str|None — refusal message when a /report target
                    isn't allowed in this scope (project membership gate);
                    None/omitted means allowed.
"""
import logging

from helpers.agent import stream_agent
from helpers.llm_helpers import _sse, friendly_llm_error
from helpers.pin_flow import stream_pin_flow
from helpers.request_utils import stream_samples_report
from store import list_pinned_study_meta

logger = logging.getLogger(__name__)


def _pinned_done_payload(chat_id, scope):
    meta = list_pinned_study_meta(chat_id, scope)
    return {"chat_id": chat_id, "persisted": True,
            "pinned_studies": [m["study_id"] for m in meta],
            "pinned_study_meta": meta}


def stream_chat_turn(*, scope, chat_id, model, user_content, report_study_id,
                     pin_study_ids, system_prompt, tools, full_msgs, persist,
                     build_context, report_guard=None, deep_search=False):
    yield ': keepalive\n\n'
    assistant_parts = []
    ui_payload      = None
    try:
        if pin_study_ids is not None:
            all_pinned = yield from stream_pin_flow(
                pin_study_ids=pin_study_ids,
                chat_id=chat_id,
                scope=scope,
                full_msgs=full_msgs,
                model=model,
                system_prompt=system_prompt,
                persist=persist,
            )
            done = _pinned_done_payload(chat_id, scope)
            done["pinned_studies"] = all_pinned
            yield _sse("done", done)
            return

        if report_study_id is not None:
            refusal = report_guard(report_study_id) if report_guard else None
            if refusal is not None:
                yield _sse("step_start", {"name": "load_samples",
                                          "label": f"Loading sample data for study {report_study_id}…"})
                yield _sse("step_done", {"name": "load_samples",
                                         "label": f"Study {report_study_id} is not part of this project"})
                yield _sse("token", {"token": refusal})
                assistant_parts = [refusal]
            else:
                assistant_parts, ui_payload = yield from stream_samples_report(report_study_id)
        else:
            combined_ctx = yield from build_context()

            segments_list = []
            current_text  = []
            for event in stream_agent(
                full_msgs,
                system_prompt=system_prompt,
                model=model,
                study_context_text=combined_ctx,
                scope=scope,
                chat_id=chat_id,
                deep_search=deep_search,
                tools=tools,
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
                            seg["result"] = {"label": event["label"], "detail": event["detail"],
                                             "ui_payload": event.get("ui_payload")}
                            break
                    yield _sse("segment_tool_result", {"name": event["name"], "label": event["label"],
                                                       "detail": event.get("detail", ""),
                                                       "ui_payload": event.get("ui_payload")})
            if current_text:
                segments_list.append({"type": "text", "content": "".join(current_text), "done": True})
            if segments_list:
                ui_payload = {"kind": "agent_segments", "segments": segments_list}

        assistant_content = "".join(assistant_parts).strip()
        persist(assistant_content, ui_payload)
        if ui_payload and ui_payload.get("kind") == "agent_segments":
            yield _sse("done", _pinned_done_payload(chat_id, scope))
        else:
            yield _sse("done", {"chat_id": chat_id, "persisted": True})
    except Exception as e:
        logger.exception("stream error in %s chat %s", scope, chat_id)
        yield _sse("error", {"error": friendly_llm_error(e, model)})
