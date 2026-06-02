"""Streaming tool-calling loop for the agentic chatbot."""

import json
import logging
import time
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
    Streaming agentic loop. Yields typed dicts for the route to forward as SSE:
      {"type": "agent_start"}                                      — once at top
      {"type": "token",             "token": str}                  — LLM text
      {"type": "segment_tool_call", "name": str, "label": str, "args": dict}
      {"type": "segment_tool_result","name": str, "label": str,
                                     "detail": str, "ui_payload": dict|None}
    Raises on unrecoverable errors; callers should catch and emit an SSE error.
    """
    resolved = _resolve_model(model)
    api_msgs = _build_api_messages(messages, study_context_text, system_prompt)

    yield {"type": "agent_start"}

    for iteration in range(max_iters):
        t_llm = time.perf_counter()
        stream = client.chat.completions.create(
            model=resolved,
            messages=api_msgs,
            tools=TOOL_SCHEMAS,
            stream=True,
            timeout=300.0,
        )

        content_parts = []
        tool_call_map = {}   # index -> {id, name, arguments}
        finish_reason = None
        ttft = None

        for chunk in stream:
            if not chunk.choices:
                continue
            if ttft is None:
                ttft = time.perf_counter() - t_llm
            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta

            if delta.content:
                content_parts.append(delta.content)
                yield {"type": "token", "token": delta.content}

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

        logger.info("[timing] llm_round iter=%d ttft=%.3fs total=%.3fs",
                    iteration, ttft or -1, time.perf_counter() - t_llm)

        assistant_content = "".join(content_parts)

        if finish_reason != "tool_calls" or not tool_call_map:
            break

        ordered_calls = [tool_call_map[i] for i in sorted(tool_call_map)]
        api_msgs.append({
            "role":    "assistant",
            "content": assistant_content or None,
            "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in ordered_calls
            ],
        })

        for tc in ordered_calls:
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}

            step_name = f"tool_{name}_{tc['id'][:6]}"
            yield {"type": "segment_tool_call", "name": step_name,
                   "label": _tool_label(name, args), "args": args}

            t0 = time.perf_counter()
            try:
                result = execute_tool(name, args, scope=scope, chat_id=chat_id)
            except Exception as exc:
                dt = time.perf_counter() - t0
                logger.exception("tool %s raised after %.3fs", name, dt)
                yield {"type": "segment_tool_result", "name": step_name,
                       "label": f"{name} failed",
                       "detail": f"{str(exc)[:60]} · {dt:.1f}s",
                       "ui_payload": None}
                api_msgs.append({"role": "tool", "tool_call_id": tc["id"],
                                  "content": f"Tool {name} failed: {exc}"})
                continue

            dt = time.perf_counter() - t0
            logger.info("[timing] tool=%s elapsed=%.3fs", name, dt)
            detail = f"{result.detail} · {dt:.1f}s" if result.detail else f"{dt:.1f}s"
            yield {"type": "segment_tool_result", "name": step_name,
                   "label": result.label, "detail": detail,
                   "ui_payload": result.ui_payload}
            api_msgs.append({"role": "tool", "tool_call_id": tc["id"],
                              "content": result.text})

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
