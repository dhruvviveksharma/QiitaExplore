"""Streaming tool-calling loop for the agentic chatbot."""

import json
import logging
import time
from typing import Generator, Optional

from config import client, get_client
from helpers.llm_helpers import _build_api_messages, _extract_system_and_messages, _resolve_model
from helpers.agent_tools import execute_tool

logger = logging.getLogger(__name__)

_DEDUP_AFTER_USE_TOOL_NAMES = frozenset({"search_studies", "search_project_studies"})


def _openai_tools_to_anthropic(tools):
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"].get("description", ""),
            "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


def _is_dedup_search_tool(name: str) -> bool:
    return name in _DEDUP_AFTER_USE_TOOL_NAMES


def _tools_without_dedup_searches(tools, search_already_done: bool):
    if not search_already_done:
        return tools
    blocked = _DEDUP_AFTER_USE_TOOL_NAMES
    # tools is OpenAI-shape ({"function": {"name": ...}}) on the OpenAI path,
    # Anthropic-shape ({"name": ...}) on the Anthropic path — handle both.
    return [t for t in tools if t.get("function", t)["name"] not in blocked]


def _execute_tool_call(name, args, call_id, *, scope, chat_id, deep_search, search_already_done):
    """Yield segment events for one tool call; return (result_text, is_dedup_search)."""
    step_name = f"tool_{name}_{call_id[:6]}"
    yield {"type": "segment_tool_call", "name": step_name,
           "label": _tool_label(name, args), "args": args}
    if _is_dedup_search_tool(name) and search_already_done:
        msg = f"Only one {name} call is allowed per turn. Use the results already returned."
        yield {"type": "segment_tool_result", "name": step_name,
               "label": f"{name} skipped", "detail": "duplicate search", "ui_payload": None}
        return (msg, True)
    t0 = time.perf_counter()
    try:
        result = execute_tool(name, args, scope=scope, chat_id=chat_id, deep_search=deep_search)
    except Exception as exc:
        dt = time.perf_counter() - t0
        logger.exception("tool %s raised after %.3fs", name, dt)
        yield {"type": "segment_tool_result", "name": step_name,
               "label": f"{name} failed",
               "detail": f"{str(exc)[:60]} · {dt:.1f}s", "ui_payload": None}
        return (f"Tool {name} failed: {exc}", False)
    dt = time.perf_counter() - t0
    logger.info("[timing] tool=%s elapsed=%.3fs result_chars=%d", name, dt, len(result.text or ""))
    detail = f"{result.detail} · {dt:.1f}s" if result.detail else f"{dt:.1f}s"
    yield {"type": "segment_tool_result", "name": step_name,
           "label": result.label, "detail": detail, "ui_payload": result.ui_payload}
    return (result.text, _is_dedup_search_tool(name))


def _stream_anthropic_agent(anth_client, api_msgs, resolved, scope, chat_id, deep_search, max_iters, tools):
    anth_tools = _openai_tools_to_anthropic(tools)
    msgs = list(api_msgs)
    search_already_done = False
    final_had_synthesis = False

    for iteration in range(max_iters):
        curr_tools = _tools_without_dedup_searches(anth_tools, search_already_done)
        system_text, messages = _extract_system_and_messages(msgs)
        t_llm = time.perf_counter()
        ttft = None
        content_parts = []
        tool_uses = []
        current_block = None
        current_json = ""
        stop_reason = None

        with anth_client.messages.stream(
            model=resolved,
            max_tokens=4096,
            system=system_text,
            messages=messages,
            tools=curr_tools,
        ) as stream:
            for event in stream:
                if ttft is None:
                    ttft = time.perf_counter() - t_llm
                etype = getattr(event, "type", None)
                if etype == "content_block_start":
                    cb = event.content_block
                    if cb.type == "tool_use":
                        current_block = {"id": cb.id, "name": cb.name}
                        current_json = ""
                    else:
                        current_block = None
                elif etype == "content_block_delta":
                    d = event.delta
                    dtype = getattr(d, "type", None)
                    if dtype == "text_delta" and d.text:
                        content_parts.append(d.text)
                        yield {"type": "token", "token": d.text}
                    elif dtype == "input_json_delta":
                        current_json += d.partial_json or ""
                elif etype == "content_block_stop":
                    if current_block is not None:
                        try:
                            parsed = json.loads(current_json or "{}")
                        except json.JSONDecodeError:
                            parsed = {}
                        tool_uses.append({
                            "id": current_block["id"],
                            "name": current_block["name"],
                            "args": parsed,
                        })
                        current_block = None
                elif etype == "message_delta":
                    stop_reason = getattr(event.delta, "stop_reason", None)

        elapsed = time.perf_counter() - t_llm
        logger.info(
            "[anthropic round %d] ttft=%.3fs total=%.3fs content=%d stop=%s tools=%d",
            iteration, ttft or -1, elapsed, len("".join(content_parts)), stop_reason, len(tool_uses),
        )

        if stop_reason != "tool_use" or not tool_uses:
            final_had_synthesis = bool(content_parts)
            break

        asst_content = []
        if content_parts:
            asst_content.append({"type": "text", "text": "".join(content_parts)})
        for tu in tool_uses:
            asst_content.append({"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tu["args"]})
        msgs.append({"role": "assistant", "content": asst_content})

        tool_results = []
        for tu in tool_uses:
            result_text, is_search = yield from _execute_tool_call(
                tu["name"], tu["args"], tu["id"],
                scope=scope, chat_id=chat_id, deep_search=deep_search,
                search_already_done=search_already_done,
            )
            if is_search:
                search_already_done = True
            tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": result_text})
        msgs.append({"role": "user", "content": tool_results})

    if iteration == max_iters - 1 and stop_reason == "tool_use":
        logger.warning("anthropic agent hit max_iters=%d without stopping", max_iters)

    if not final_had_synthesis and msgs and isinstance(msgs[-1].get("content"), list) and \
            any(c.get("type") == "tool_result" for c in msgs[-1]["content"]):
        logger.info("[anthropic agent] forcing synthesis — loop ended on tool results")
        sys_txt, anth_msgs = _extract_system_and_messages(msgs)
        with anth_client.messages.stream(
            model=resolved, max_tokens=4096, system=sys_txt, messages=anth_msgs,
        ) as stream:
            for event in stream:
                if getattr(event, "type", None) == "content_block_delta":
                    d = event.delta
                    if getattr(d, "type", None) == "text_delta" and d.text:
                        yield {"type": "token", "token": d.text}


def stream_agent(
    messages: list,
    *,
    system_prompt: str,
    model: str,
    study_context_text: Optional[str],
    scope: str,
    chat_id: str,
    tools: list,
    max_iters: int = 4,
    deep_search: bool = False,
) -> Generator[dict, None, None]:
    """
    Streaming agentic loop. Yields typed dicts for the route to forward as SSE:
      {"type": "agent_start"}                                      — once at top
      {"type": "token",             "token": str}                  — LLM text
      {"type": "reasoning",         "token": str}                  — reasoning model thinking
      {"type": "segment_tool_call", "name": str, "label": str, "args": dict}
      {"type": "segment_tool_result","name": str, "label": str,
                                     "detail": str, "ui_payload": dict|None}
    Raises on unrecoverable errors; callers should catch and emit an SSE error.
    """
    resolved = _resolve_model(model)
    api_msgs = _build_api_messages(messages, study_context_text, system_prompt)

    logger.info("[agent_start] model=%s resolved=%s deep=%s msg_count=%d",
                model, resolved, deep_search, len(api_msgs))

    yield {"type": "agent_start"}

    llm_client, provider = get_client(resolved)
    if provider == "anthropic":
        yield from _stream_anthropic_agent(
            llm_client, api_msgs, resolved, scope, chat_id, deep_search, max_iters, tools)
        return

    search_already_done = False
    final_had_synthesis = False

    for iteration in range(max_iters):
        active_tools = _tools_without_dedup_searches(tools, search_already_done)

        t_llm = time.perf_counter()
        stream = client.chat.completions.create(
            model=resolved,
            messages=api_msgs,
            tools=active_tools,
            stream=True,
            timeout=300.0,
        )

        content_parts   = []
        reasoning_parts = []
        tool_call_map   = {}
        finish_reason   = None
        ttft            = None

        for chunk in stream:
            if not chunk.choices:
                continue
            if ttft is None:
                ttft = time.perf_counter() - t_llm
            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
                yield {"type": "reasoning", "token": reasoning}

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

            if logger.isEnabledFor(logging.DEBUG):
                has_c = bool(delta.content)
                has_r = bool(reasoning)
                has_t = bool(delta.tool_calls)
                fr    = choice.finish_reason
                if has_c or has_r or has_t or fr:
                    logger.debug(
                        "[chunk iter=%d] content=%d reasoning=%d tools=%d finish=%s",
                        iteration,
                        len(delta.content or "") if has_c else 0,
                        len(reasoning or "") if has_r else 0,
                        len(delta.tool_calls or []) if has_t else 0,
                        fr or "-",
                    )

        elapsed = time.perf_counter() - t_llm
        assistant_content   = "".join(content_parts)
        assistant_reasoning = "".join(reasoning_parts)

        logger.info(
            "[round %d] ttft=%.3fs total=%.3fs content=%d chars reasoning=%d chars "
            "finish=%s tool_calls=%d",
            iteration, ttft or -1, elapsed,
            len(assistant_content), len(assistant_reasoning),
            finish_reason, len(tool_call_map),
        )

        if finish_reason != "tool_calls" or not tool_call_map:
            final_had_synthesis = bool(content_parts)
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
            result_text, is_search = yield from _execute_tool_call(
                name, args, tc["id"],
                scope=scope, chat_id=chat_id, deep_search=deep_search,
                search_already_done=search_already_done,
            )
            if is_search:
                search_already_done = True
            api_msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": result_text})

    if iteration == max_iters - 1 and finish_reason == "tool_calls":
        logger.warning("agent hit max_iters=%d without stopping", max_iters)

    if not final_had_synthesis and api_msgs and api_msgs[-1].get("role") == "tool":
        logger.info("[agent] forcing synthesis — loop ended on tool results")
        synth = client.chat.completions.create(
            model=resolved, messages=api_msgs, stream=True, timeout=300.0,
        )
        for chunk in synth:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield {"type": "token", "token": delta.content}


def _tool_label(name: str, args: dict) -> str:
    """Human-readable step label for a tool call while it runs."""
    if name == "search_studies":
        kws = (args.get("organism") or args.get("keywords") or
               args.get("qualifier") or args.get("body_site") or [])[:3]
        return f"Searching: {', '.join(kws)}…" if kws else "Searching Qiita…"
    if name == "search_project_studies":
        kws = args.get("keywords") or []
        return f"Searching project: {', '.join(kws[:3])}…" if kws else "Listing project studies…"
    if name == "get_study_report":
        return f"Loading report for study {args.get('study_id', '?')}…"
    if name == "get_project_study_report":
        return f"Loading project report for study {args.get('study_id', '?')}…"
    if name == "pin_study":
        ids = args.get("study_ids") or []
        return f"Pinning {len(ids)} {'study' if len(ids) == 1 else 'studies'}…"
    if name == "search_by_sample":
        ff  = args.get("field_filters") or []
        kws = args.get("keywords") or []
        parts = [f"{f['field']}={f['value']}" for f in ff[:2]] + kws[:2]
        return f"Sample search: {', '.join(parts)}…" if parts else "Searching sample metadata…"
    return f"Running {name}…"
