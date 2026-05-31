"""Streaming tool-calling loop for the agentic chatbot."""

import json
import logging
from typing import Generator, Optional

from config import client
from helpers.llm_helpers import _build_api_messages, _resolve_model, friendly_llm_error
from helpers.agent_tools import TOOL_SCHEMAS, execute_tool

logger = logging.getLogger(__name__)


def stream_agent(
    messages: list,
    *,
    system_prompt: str,
    model: str,
    study_context_text: Optional[str],
    scope: str,
    chat_id: str,
    max_iters: int = 4,
) -> Generator[dict, None, None]:
    """
    Streaming agentic loop. Yields typed dicts that callers translate to SSE:
      {"type": "token",      "token": str}
      {"type": "step_start", "name": str, "label": str}
      {"type": "step_done",  "name": str, "label": str, "detail": str}
      {"type": "ui",         "payload": dict}
    Raises on unrecoverable errors; callers should catch and emit an SSE error.
    """
    resolved = _resolve_model(model)
    # Start with the full conversation history + study context in the system prompt
    api_msgs = _build_api_messages(messages, study_context_text, system_prompt)

    for iteration in range(max_iters):
        stream = client.chat.completions.create(
            model=resolved,
            messages=api_msgs,
            tools=TOOL_SCHEMAS,
            stream=True,
            timeout=300.0,
        )

        # Accumulate a full turn from the stream
        content_parts: list[str] = []
        tool_call_map: dict[int, dict] = {}  # index -> {id, name, arguments}
        finish_reason = None

        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta

            # Stream content tokens
            if delta.content:
                content_parts.append(delta.content)
                yield {"type": "token", "token": delta.content}

            # Accumulate tool call fragments (may arrive across many chunks)
            for tc in (delta.tool_calls or []):
                idx = tc.index
                if idx not in tool_call_map:
                    tool_call_map[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                if tc.id:
                    tool_call_map[idx]["id"] = tc.id
                fn = tc.function
                if fn:
                    if fn.name:
                        tool_call_map[idx]["name"] += fn.name
                    if fn.arguments:
                        tool_call_map[idx]["arguments"] += fn.arguments

        assistant_content = "".join(content_parts)

        if finish_reason != "tool_calls" or not tool_call_map:
            # Model is done — normal text response
            break

        # Build the assistant turn with tool_calls for the API history
        ordered_calls = [tool_call_map[i] for i in sorted(tool_call_map)]
        tool_calls_for_api = [
            {
                "id":       tc["id"],
                "type":     "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }
            for tc in ordered_calls
        ]
        api_msgs.append({
            "role":       "assistant",
            "content":    assistant_content or None,
            "tool_calls": tool_calls_for_api,
        })

        # Execute each tool call, emit step events, build tool result messages
        for tc in ordered_calls:
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}

            step_name = f"tool_{name}_{tc['id'][:6]}"
            yield {"type": "step_start", "name": step_name, "label": _tool_label(name, args)}

            try:
                result = execute_tool(name, args, scope=scope, chat_id=chat_id)
            except Exception as exc:
                logger.exception("tool %s raised", name)
                result_text = f"Tool {name} failed: {exc}"
                yield {"type": "step_done", "name": step_name, "label": f"{name} failed", "detail": str(exc)[:80]}
                api_msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": result_text})
                continue

            yield {"type": "step_done", "name": step_name, "label": result.label, "detail": result.detail}
            if result.ui_payload:
                yield {"type": "ui", "payload": result.ui_payload}
            api_msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": result.text})

    # Ensure iteration exhaustion is not silently swallowed
    if iteration == max_iters - 1 and finish_reason == "tool_calls":
        logger.warning("agent hit max_iters=%d without stopping", max_iters)


def _tool_label(name: str, args: dict) -> str:
    """Human-readable step label for a tool call while it runs."""
    if name == "search_studies":
        kws = (args.get("keywords") or [])[:3]
        return f"Searching: {', '.join(kws)}…" if kws else "Searching Qiita…"
    if name == "get_study_report":
        return f"Loading report for study {args.get('study_id', '?')}…"
    if name == "pin_study":
        ids = args.get("study_ids") or []
        return f"Pinning {len(ids)} {'study' if len(ids) == 1 else 'studies'}…"
    if name == "compute_diversity":
        return "Computing diversity…"
    return f"Running {name}…"
