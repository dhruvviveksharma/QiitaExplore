"""Pure reducer: pi AgentSessionEvents -> the existing 10-event SSE contract
and the persisted segments list.

Single source of truth for both, closing the dual-authoring hazard
docs/06-streaming-and-chat.md calls its headline risk (today the segment
array is built independently in Python and JS from a Python-specific event
shape). translate() and build_segments() both consume the same pi event
sequence and require no frontend changes: the segment/event shapes emitted
here are byte-identical to what helpers/agent.py + global_chat_routes.py
produce today.

Turn completion: the sidecar only closes the NDJSON stream after the turn has
fully settled (agent_settled, not agent_end's own willRetry-agnostic
resolution) — see pi_sidecar/sessions.mjs. So by the time the caller's event
iterable is exhausted, the turn is done; neither function here needs to
reason about pi's own retry/settlement semantics. Callers are responsible for
emitting `done` themselves afterward (this module never does, exactly as
helpers/agent.py's stream_agent() never does today — that's the route's job).
"""

import logging

from helpers.tool_labels import _tool_label

logger = logging.getLogger(__name__)


def _tool_step_name(tool_name: str, tool_call_id: str) -> str:
    """Byte-identical to helpers/agent.py:28 — this string is the
    call<->result correlation key on both the server reducer and
    app_state.js:314-322. Get it right and the frontend needs no changes."""
    return f"tool_{tool_name}_{(tool_call_id or '')[:6]}"


def _first_text(result: dict) -> str:
    for block in (result or {}).get("content") or []:
        if block.get("type") == "text":
            return block.get("text") or ""
    return ""


def _compaction_detail(event: dict) -> str:
    if event.get("errorMessage"):
        return event["errorMessage"]
    if event.get("aborted"):
        return "aborted"
    result = event.get("result") or {}
    before, after = result.get("tokensBefore"), result.get("estimatedTokensAfter")
    if before is not None and after is not None:
        return f"{before} → {after} tokens"
    return "done"


class _ToolTimer:
    """Elapsed-time tracking for the '· {t:.1f}s' detail suffix
    (helpers/agent.py:41-46 does this with a local t0/time.perf_counter());
    pi's tool_execution_start/end events carry no timestamp of their own, so
    the translator tracks wall-clock time itself, keyed by the same
    correlation name used for call<->result matching."""

    def __init__(self):
        self._started_at = {}

    def start(self, name: str):
        import time
        self._started_at[name] = time.perf_counter()

    def elapsed(self, name: str):
        import time
        t0 = self._started_at.pop(name, None)
        return (time.perf_counter() - t0) if t0 is not None else None


def translate(pi_events):
    """Yield (sse_event_name, payload) for each pi event, live, single-pass."""
    started = False
    timer = _ToolTimer()

    for event in pi_events:
        etype = event.get("type")

        if not started:
            started = True
            yield ("agent_start", {})

        if etype == "message_update":
            ame = event.get("assistantMessageEvent") or {}
            if ame.get("type") == "text_delta":
                yield ("token", {"token": ame.get("delta", "")})
            elif ame.get("type") == "error":
                err_msg = (ame.get("error") or {}).get("errorMessage") or "The model returned an error."
                yield ("error", {"error": err_msg})

        elif etype == "tool_execution_start":
            name = _tool_step_name(event.get("toolName"), event.get("toolCallId"))
            timer.start(name)
            yield ("segment_tool_call", {
                "name": name,
                "label": _tool_label(event.get("toolName"), event.get("args") or {}),
                "args": event.get("args") or {},
            })

        elif etype == "tool_execution_end":
            name = _tool_step_name(event.get("toolName"), event.get("toolCallId"))
            dt = timer.elapsed(name)
            suffix = f" · {dt:.1f}s" if dt is not None else ""
            result = event.get("result") or {}
            if event.get("isError"):
                text = _first_text(result)
                yield ("segment_tool_result", {
                    "name": name,
                    "label": f"{event.get('toolName')} failed",
                    "detail": f"{text[:60]}{suffix}" if text else suffix.strip(" ·") or "failed",
                    "ui_payload": None,
                })
            else:
                details = result.get("details") or {}
                base_detail = details.get("detail") or ""
                detail = f"{base_detail}{suffix}" if base_detail else suffix.strip(" ·")
                yield ("segment_tool_result", {
                    "name": name,
                    "label": details.get("label") or f"{event.get('toolName')} complete",
                    "detail": detail,
                    "ui_payload": details.get("ui_payload"),
                })

        elif etype == "compaction_start":
            yield ("step_start", {"name": "compaction", "label": "Compacting conversation history…"})
        elif etype == "compaction_end":
            yield ("step_done", {
                "name": "compaction", "label": "History compacted",
                "detail": _compaction_detail(event),
            })

        elif etype == "auto_retry_start":
            yield ("step_start", {
                "name": "retry",
                "label": f"Retrying (attempt {event.get('attempt')}/{event.get('maxAttempts')})…",
            })
        elif etype == "auto_retry_end":
            yield ("step_done", {
                "name": "retry",
                "label": "Retry succeeded" if event.get("success") else "Retry failed",
                "detail": event.get("finalError") or "",
            })

        elif etype == "sidecar_error":
            yield ("error", {"error": event.get("error") or "pi sidecar error"})

        elif etype == "agent_end":
            _log_usage(event)

        # turn_start / turn_end / message_start / message_end / agent_settled /
        # queue_update / thinking_level_changed / session_info_changed /
        # bash_execution_update / entry_appended / summarization_retry_* /
        # toolcall_delta / thinking_delta: no SSE equivalent today —
        # deliberately dropped rather than invented.


def build_segments(pi_events):
    """The persisted ui_payload['segments'] list — same shape
    global_chat_routes.py:137-154 builds today, computed from the same event
    sequence translate() consumes."""
    segments = []
    current_text = []
    timer = _ToolTimer()

    def flush_text():
        if current_text:
            segments.append({"type": "text", "content": "".join(current_text), "done": True})
            current_text.clear()

    for event in pi_events:
        etype = event.get("type")

        if etype == "message_update":
            ame = event.get("assistantMessageEvent") or {}
            if ame.get("type") == "text_delta":
                current_text.append(ame.get("delta", ""))

        elif etype == "tool_execution_start":
            flush_text()
            name = _tool_step_name(event.get("toolName"), event.get("toolCallId"))
            timer.start(name)
            segments.append({
                "type": "tool", "name": name,
                "label": _tool_label(event.get("toolName"), event.get("args") or {}),
                "args": event.get("args") or {},
                "done": False, "result": None,
            })

        elif etype == "tool_execution_end":
            name = _tool_step_name(event.get("toolName"), event.get("toolCallId"))
            dt = timer.elapsed(name)
            suffix = f" · {dt:.1f}s" if dt is not None else ""
            result = event.get("result") or {}
            for seg in segments:
                if seg.get("type") == "tool" and seg.get("name") == name and not seg.get("done"):
                    seg["done"] = True
                    if event.get("isError"):
                        text = _first_text(result)
                        seg["result"] = {
                            "label": f"{event.get('toolName')} failed",
                            "detail": f"{text[:60]}{suffix}" if text else suffix.strip(" ·") or "failed",
                            "ui_payload": None,
                        }
                    else:
                        details = result.get("details") or {}
                        base_detail = details.get("detail") or ""
                        seg["result"] = {
                            "label": details.get("label") or f"{event.get('toolName')} complete",
                            "detail": f"{base_detail}{suffix}" if base_detail else suffix.strip(" ·"),
                            "ui_payload": details.get("ui_payload"),
                        }
                    break

    flush_text()
    return segments


def _log_usage(agent_end_event: dict):
    """Cost/token visibility only — never put on the SSE wire (Phase 3 scope)."""
    messages = agent_end_event.get("messages") or []
    for msg in reversed(messages):
        usage = msg.get("usage")
        if usage:
            logger.info(
                "[pi] turn usage: input=%s output=%s cacheRead=%s cacheWrite=%s cost=%s",
                usage.get("input"), usage.get("output"),
                usage.get("cacheRead"), usage.get("cacheWrite"), usage.get("cost"),
            )
            return
