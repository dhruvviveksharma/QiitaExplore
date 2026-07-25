"""Pure reducer: pi AgentSessionEvents -> the existing 10-event SSE contract
and the persisted segments list.

Single source of truth for both, closing the dual-authoring hazard
docs/06-streaming-and-chat.md calls its headline risk (today the segment
array is built independently in Python and JS from a Python-specific event
shape). The SSE events and segments emitted here are byte-identical to what
helpers/agent.py + global_chat_routes.py produce, so the frontend needs no
changes.

Segments are derived from the SSE events, not from a second walk over the pi
stream. That matters for more than tidiness: an earlier version ran a separate
pass after the stream had finished, so its elapsed-time clock measured the
replay rather than the tool call and every persisted tool card rendered
"· 0.0s" while the live stream had shown the real duration.

Turn completion: the sidecar only closes the NDJSON stream after the turn has
fully settled (agent_settled, not agent_end's own willRetry-agnostic
resolution) — see pi_sidecar/sessions.mjs. So by the time the caller's event
iterable is exhausted, the turn is done; nothing here needs to reason about
pi's retry/settlement semantics. Callers emit `done` themselves afterward,
exactly as helpers/agent.py's stream_agent() never does — that's the route's job.
"""

import logging
import time

from helpers.tool_labels import _tool_label

logger = logging.getLogger(__name__)

# pi events that mean the model has actually started working. `agent_start` is
# synthesized on the first of these rather than on the first event of any kind:
# a sidecar that dies before the model speaks emits only `sidecar_error`, and
# announcing agent_start for that would flip the frontend into segments mode
# (app_render.js renders AgentMessageBubble once m.segments !== null) and hide
# the error text in an empty bubble.
_TURN_STARTED_EVENTS = frozenset({
    "agent_start", "turn_start", "message_start", "message_update",
    "tool_execution_start", "compaction_start", "auto_retry_start",
})


def _tool_step_name(tool_name: str, tool_call_id: str) -> str:
    """Byte-identical to helpers/agent.py:29 — this string is the
    call<->result correlation key on both the server reducer and
    app_state.js. Get it right and the frontend needs no changes."""
    return f"tool_{tool_name}_{(tool_call_id or '')[:6]}"


def _first_text(result: dict) -> str:
    for block in (result or {}).get("content") or []:
        if block.get("type") == "text":
            return block.get("text") or ""
    return ""


def _detail_with_elapsed(base: str, dt) -> str:
    """`{base} · {dt:.1f}s`, degrading cleanly when either half is missing.
    helpers/agent.py:41-46 builds the same suffix from its own perf_counter."""
    if dt is None:
        return base
    return f"{base} · {dt:.1f}s" if base else f"{dt:.1f}s"


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


class TurnTranslator:
    """One walk over the pi event stream: yields SSE events live, and
    accumulates the persisted segment list as a side effect.

    pi's tool_execution_start/end carry no timestamps, so elapsed time is
    measured here — which is only correct because this runs while the stream
    is live. Building segments in a second pass is what produced the "· 0.0s"
    regression, so segments deliberately reuse this pass's timings rather than
    re-deriving their own.
    """

    def __init__(self):
        self.segments = []
        self._started = False
        self._tool_started_at = {}
        self._text = []

    # ── segment accumulation ────────────────────────────────────────────────
    def _flush_text(self):
        if self._text:
            self.segments.append({"type": "text", "content": "".join(self._text), "done": True})
            self._text = []

    def _open_tool_segment(self, payload):
        self._flush_text()
        self.segments.append({
            "type": "tool", "name": payload["name"], "label": payload["label"],
            "args": payload["args"], "done": False, "result": None,
        })

    def _close_tool_segment(self, payload):
        for seg in self.segments:
            if seg.get("type") == "tool" and seg.get("name") == payload["name"] and not seg.get("done"):
                seg["done"] = True
                seg["result"] = {
                    "label": payload["label"],
                    "detail": payload["detail"],
                    "ui_payload": payload["ui_payload"],
                }
                break

    # ── the walk ────────────────────────────────────────────────────────────
    def run(self, pi_events):
        """Yield (sse_event_name, payload) per pi event. Lazy: safe to iterate
        straight into an SSE response without buffering the stream."""
        for event in pi_events:
            for sse_name, payload in self._handle(event):
                if sse_name == "token":
                    self._text.append(payload["token"])
                elif sse_name == "segment_tool_call":
                    self._open_tool_segment(payload)
                elif sse_name == "segment_tool_result":
                    self._close_tool_segment(payload)
                yield (sse_name, payload)
        self._flush_text()

    def _handle(self, event):
        etype = event.get("type")

        if not self._started and etype in _TURN_STARTED_EVENTS:
            self._started = True
            yield ("agent_start", {})

        if etype == "message_update":
            ame = event.get("assistantMessageEvent") or {}
            if ame.get("type") == "text_delta":
                yield ("token", {"token": ame.get("delta", "")})
            elif ame.get("type") == "error":
                err = (ame.get("error") or {}).get("errorMessage") or "The model returned an error."
                yield ("error", {"error": err})

        elif etype == "tool_execution_start":
            name = _tool_step_name(event.get("toolName"), event.get("toolCallId"))
            self._tool_started_at[name] = time.perf_counter()
            yield ("segment_tool_call", {
                "name": name,
                "label": _tool_label(event.get("toolName"), event.get("args") or {}),
                "args": event.get("args") or {},
            })

        elif etype == "tool_execution_end":
            name = _tool_step_name(event.get("toolName"), event.get("toolCallId"))
            t0 = self._tool_started_at.pop(name, None)
            dt = (time.perf_counter() - t0) if t0 is not None else None
            result = event.get("result") or {}
            if event.get("isError"):
                text = _first_text(result)
                yield ("segment_tool_result", {
                    "name": name,
                    "label": f"{event.get('toolName')} failed",
                    "detail": _detail_with_elapsed(text[:60], dt) or "failed",
                    "ui_payload": None,
                })
            else:
                details = result.get("details") or {}
                yield ("segment_tool_result", {
                    "name": name,
                    "label": details.get("label") or f"{event.get('toolName')} complete",
                    "detail": _detail_with_elapsed(details.get("detail") or "", dt),
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


def translate(pi_events):
    """Yield (sse_event_name, payload) for each pi event, live, single-pass.

    Callers that also need the persisted segments should drive a TurnTranslator
    directly and read `.segments` afterward, rather than calling build_segments()
    on a buffered copy of the stream."""
    yield from TurnTranslator().run(pi_events)


def build_segments(pi_events):
    """The persisted ui_payload['segments'] list.

    Convenience wrapper for callers that already hold the full event list (and
    for the parity tests). Note the elapsed times will be ~0 when replaying a
    buffered stream, because the tool calls have already happened — live callers
    must use TurnTranslator.run() and read `.segments`, not this."""
    t = TurnTranslator()
    for _ in t.run(pi_events):
        pass
    return t.segments


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
