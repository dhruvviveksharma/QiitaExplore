"""Streaming tool-calling loop for the agentic chatbot."""

import json
import logging
import time
from typing import Generator, Optional

from config import get_client, SEARCH_CALLS_PER_MESSAGE
from helpers.llm_helpers import _build_api_messages, _extract_system_and_messages, _resolve_model
from helpers.agent_tools import execute_tool
from helpers.chat_transcript import rows_to_provider_messages
from helpers.llm_retry import run_with_retry
from helpers.turn_log import log_turn_event

logger = logging.getLogger(__name__)

_BUDGETED_SEARCH_TOOL_NAMES = frozenset({"search_studies", "search_project_studies"})


def _openai_tools_to_anthropic(tools):
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"].get("description", ""),
            "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


def _no_final_answer_text(max_iters, last_tool_failure, reason):
    """Guaranteed non-empty text so a silent turn never reads as the model dying.
    reason: "rounds" = max_iters exhausted; "synthesis" = stopped after tool calls
    short of the limit, synthesis empty; "empty" = no text and no tool calls."""
    if reason == "empty":
        msg = ("\n\n_(The model returned an empty response — no text and no "
               "tool calls. Try again or rephrase.)_")
    else:
        why = (f"I ran out of tool rounds ({max_iters})" if reason == "rounds"
               else "The model stopped after its tool calls")
        msg = (f"\n\n_({why} before writing a final answer — the tool results "
               f"above are everything gathered this turn. Ask a follow-up to continue.)_")
    if last_tool_failure:
        msg += f"\n\n_(Last tool error: {last_tool_failure[:200]})_"
    return msg


def _emit_round_limit_notice(chat_id, max_iters, provider):
    """Visible 'writing final answer' step on round-budget exhaustion —
    shared by both loops so they cannot drift."""
    logger.warning("%s agent hit max_iters=%d without stopping", provider, max_iters)
    log_turn_event(chat_id, "max_rounds_exhausted", rounds=max_iters)
    yield {"type": "step_start", "name": "synthesis",
           "label": f"Tool-round limit ({max_iters}) reached — writing final answer…"}


def _emit_silent_end(chat_id, model, event, reason, max_iters, last_tool_failure, **log_fields):
    """Log the terminal event and yield the guaranteed fallback token —
    shared by both loops so they cannot drift."""
    log_turn_event(chat_id, event, model=model, **log_fields)
    yield {"type": "token",
           "token": _no_final_answer_text(max_iters, last_tool_failure, reason)}


def _is_budgeted_search_tool(name: str) -> bool:
    return name in _BUDGETED_SEARCH_TOOL_NAMES


def _tools_within_search_budget(tools, search_calls_used: int):
    if search_calls_used < SEARCH_CALLS_PER_MESSAGE:
        return tools
    blocked = _BUDGETED_SEARCH_TOOL_NAMES
    # tools is OpenAI-shape ({"function": {"name": ...}}) on the OpenAI path,
    # Anthropic-shape ({"name": ...}) on the Anthropic path — handle both.
    return [t for t in tools if t.get("function", t)["name"] not in blocked]


def _execute_tool_call(name, args, call_id, *, scope, chat_id, deep_search, search_calls_used):
    """Yield segment events for one tool call; return (result_text,
    consumed_search_slot, failed) — `failed` is the structured fact of a raise."""
    step_name = f"tool_{name}_{call_id}"
    yield {"type": "segment_tool_call", "name": step_name,
           "label": _tool_label(name, args), "args": args}
    if _is_budgeted_search_tool(name) and search_calls_used >= SEARCH_CALLS_PER_MESSAGE:
        msg = (f"{name} has already run {search_calls_used} times this message "
               f"(max {SEARCH_CALLS_PER_MESSAGE}) — synthesize from the results you already "
               f"have. The complete ranked list of every match is shown to the user in the "
               f"results panel.")
        yield {"type": "segment_tool_result", "name": step_name,
               "label": f"{name} skipped", "detail": "search limit reached", "ui_payload": None}
        return (msg, False, False)
    t0 = time.perf_counter()
    try:
        result = execute_tool(name, args, scope=scope, chat_id=chat_id, deep_search=deep_search)
    except Exception as exc:
        dt = time.perf_counter() - t0
        logger.exception("tool %s raised after %.3fs", name, dt)
        yield {"type": "segment_tool_result", "name": step_name,
               "label": f"{name} failed",
               "detail": f"{str(exc)[:60]} · {dt:.1f}s", "ui_payload": None}
        result_text = f"Tool {name} failed: {exc}"
        log_turn_event(chat_id, "tool_fail", name=name, detail=result_text)
        # A crash is never a completed search, regardless of tool name — this
        # must stay a literal False, or a crashing search call would consume
        # one of the SEARCH_CALLS_PER_MESSAGE budget slots anyway.
        return (result_text, False, True)
    dt = time.perf_counter() - t0
    logger.info("[timing] tool=%s elapsed=%.3fs result_chars=%d", name, dt, len(result.text or ""))
    detail = f"{result.detail} · {dt:.1f}s" if result.detail else f"{dt:.1f}s"
    yield {"type": "segment_tool_result", "name": step_name,
           "label": result.label, "detail": detail, "ui_payload": result.ui_payload}
    # executed=False (e.g. the empty-input early return) doesn't consume a slot.
    return (result.text, _is_budgeted_search_tool(name) and getattr(result, "executed", True), False)


def _stream_anthropic_agent(anth_client, api_msgs, resolved, scope, chat_id, deep_search, max_iters, tools):
    anth_tools = _openai_tools_to_anthropic(tools)
    msgs = list(api_msgs)
    search_calls_used = 0
    final_had_synthesis = False
    turn_had_text = False
    last_tool_failure = None

    for iteration in range(max_iters):
        curr_tools = _tools_within_search_budget(anth_tools, search_calls_used)
        system_text, messages = _extract_system_and_messages(msgs)
        t_llm = time.perf_counter()
        ttft = None
        content_parts = []
        tool_uses = []
        stop_reason = None

        def _attempt():
            nonlocal ttft, stop_reason, t_llm
            content_parts.clear()
            tool_uses.clear()
            ttft = None
            stop_reason = None
            t_llm = time.perf_counter()
            current_block = None
            current_json = ""
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

        yield from run_with_retry(
            _attempt, model=resolved,
            has_partial_output=lambda: bool(content_parts))

        elapsed = time.perf_counter() - t_llm
        turn_had_text = turn_had_text or bool(content_parts)
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
        yield {"type": "transcript_append", "entry": {
            "role": "assistant", "text": "".join(content_parts),
            "tool_calls": [{"id": tu["id"], "name": tu["name"], "args": tu["args"]}
                           for tu in tool_uses]}}

        tool_results = []
        for tu in tool_uses:
            result_text, consumed_search_slot, failed = yield from _execute_tool_call(
                tu["name"], tu["args"], tu["id"],
                scope=scope, chat_id=chat_id, deep_search=deep_search,
                search_calls_used=search_calls_used,
            )
            if consumed_search_slot:
                search_calls_used += 1
            if failed:
                last_tool_failure = result_text
            tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": result_text})
            yield {"type": "transcript_append", "entry": {
                "role": "tool", "id": tu["id"], "name": tu["name"], "text": result_text}}
        msgs.append({"role": "user", "content": tool_results})

    exhausted = iteration == max_iters - 1 and stop_reason == "tool_use"
    if exhausted:
        yield from _emit_round_limit_notice(chat_id, max_iters, "anthropic")

    if not final_had_synthesis and msgs and isinstance(msgs[-1].get("content"), list) and \
            any(c.get("type") == "tool_result" for c in msgs[-1]["content"]):
        logger.info("[anthropic agent] forcing synthesis — loop ended on tool results")
        sys_txt, anth_msgs = _extract_system_and_messages(msgs)
        synth_parts = []

        def _synth_attempt():
            synth_parts.clear()
            with anth_client.messages.stream(
                model=resolved, max_tokens=4096, system=sys_txt, messages=anth_msgs,
            ) as stream:
                for event in stream:
                    if getattr(event, "type", None) == "content_block_delta":
                        d = event.delta
                        if getattr(d, "type", None) == "text_delta" and d.text:
                            synth_parts.append(d.text)
                            yield {"type": "token", "token": d.text}

        yield from run_with_retry(
            _synth_attempt, model=resolved,
            has_partial_output=lambda: bool(synth_parts))
        turn_had_text = turn_had_text or bool(synth_parts)
        if not synth_parts:
            yield from _emit_silent_end(chat_id, resolved, "synthesis_empty",
                                        "rounds" if exhausted else "synthesis",
                                        max_iters, last_tool_failure)
            turn_had_text = True

    if not turn_had_text:
        yield from _emit_silent_end(chat_id, resolved, "turn_ended_without_text", "empty",
                                    max_iters, last_tool_failure, finish=stop_reason)


def stream_agent(
    messages: list,
    *,
    system_prompt: str,
    model: str,
    study_context_text: Optional[str],
    scope: str,
    chat_id: str,
    tools: list,
    max_iters: int = 7,
    deep_search: bool = False,
    turn_rows: Optional[list] = None,
    user_content: Optional[str] = None,
    history_summary: Optional[str] = None,
) -> Generator[dict, None, None]:
    """
    Streaming agentic loop. Yields typed dicts for the route to forward as SSE:
      {"type": "agent_start"}                                      — once at top
      {"type": "token",             "token": str}                  — LLM text
      {"type": "reasoning",         "token": str}                  — reasoning model thinking
      {"type": "segment_tool_call", "name": str, "label": str, "args": dict}
      {"type": "segment_tool_result","name": str, "label": str,
                                     "detail": str, "ui_payload": dict|None}
      {"type": "step_start",        "name": str, "label": str}   — retry / synthesis notice
      {"type": "step_done",         "name": str, "label": str}   — retry finished
      {"type": "transcript_append", "entry": dict}                 — normalized tool
                                     exchange for the caller to persist
    When `turn_rows` is given (rows from store.chat_turn_persist.load_turn_rows
    plus the new `user_content`), history is replayed with each prior turn's
    persisted tool exchange in the current provider's wire shape — the model
    remembers earlier tool results. Without it, `messages` ({role, content}
    dicts) build the history exactly as before (harness/tests path).
    Raises on unrecoverable errors; callers should catch and emit an SSE error.
    """
    resolved = _resolve_model(model)

    logger.info("[agent_start] model=%s resolved=%s deep=%s replay=%s",
                model, resolved, deep_search, turn_rows is not None)

    yield {"type": "agent_start"}

    llm_client, provider = get_client(resolved)
    if turn_rows is not None:
        system_msg = _build_api_messages([], study_context_text, system_prompt)[0]
        if history_summary:
            system_msg = {**system_msg, "content": system_msg["content"] +
                          f"\n\nEARLIER CONVERSATION (compacted summary):\n{history_summary}"}
        api_msgs = ([system_msg] + rows_to_provider_messages(turn_rows, provider)
                    + [{"role": "user", "content": user_content or ""}])
    else:
        api_msgs = _build_api_messages(messages, study_context_text, system_prompt)
    if provider == "anthropic":
        yield from _stream_anthropic_agent(
            llm_client, api_msgs, resolved, scope, chat_id, deep_search, max_iters, tools)
        return

    search_calls_used = 0
    final_had_synthesis = False
    turn_had_text = False
    last_tool_failure = None

    for iteration in range(max_iters):
        active_tools = _tools_within_search_budget(tools, search_calls_used)

        t_llm           = time.perf_counter()
        content_parts   = []
        reasoning_parts = []
        tool_call_map   = {}
        finish_reason   = None
        ttft            = None

        def _attempt():
            # Fresh slate per attempt — a retry only ever runs when nothing
            # reached the client, so clearing internal fragments is safe.
            nonlocal finish_reason, ttft, t_llm
            content_parts.clear()
            reasoning_parts.clear()
            tool_call_map.clear()
            finish_reason = None
            ttft = None
            t_llm = time.perf_counter()
            stream = llm_client.chat.completions.create(
                model=resolved,
                messages=api_msgs,
                tools=active_tools,
                stream=True,
                timeout=300.0,
            )
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

        yield from run_with_retry(
            _attempt, model=resolved,
            has_partial_output=lambda: bool(content_parts or reasoning_parts))

        elapsed = time.perf_counter() - t_llm
        assistant_content   = "".join(content_parts)
        assistant_reasoning = "".join(reasoning_parts)
        turn_had_text = turn_had_text or bool(assistant_content)

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

        parsed_calls = []
        for tc in ordered_calls:
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            parsed_calls.append((tc, args))
        yield {"type": "transcript_append", "entry": {
            "role": "assistant", "text": assistant_content,
            "tool_calls": [{"id": tc["id"], "name": tc["name"], "args": args}
                           for tc, args in parsed_calls]}}

        for tc, args in parsed_calls:
            name = tc["name"]
            result_text, consumed_search_slot, failed = yield from _execute_tool_call(
                name, args, tc["id"],
                scope=scope, chat_id=chat_id, deep_search=deep_search,
                search_calls_used=search_calls_used,
            )
            if consumed_search_slot:
                search_calls_used += 1
            if failed:
                last_tool_failure = result_text
            api_msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": result_text})
            yield {"type": "transcript_append", "entry": {
                "role": "tool", "id": tc["id"], "name": name, "text": result_text}}

    exhausted = iteration == max_iters - 1 and finish_reason == "tool_calls"
    if exhausted:
        yield from _emit_round_limit_notice(chat_id, max_iters, "openai")

    if not final_had_synthesis and api_msgs and api_msgs[-1].get("role") == "tool":
        logger.info("[agent] forcing synthesis — loop ended on tool results")
        synth_parts = []

        def _synth_attempt():
            synth_parts.clear()
            synth = llm_client.chat.completions.create(
                model=resolved, messages=api_msgs, stream=True, timeout=300.0,
            )
            for chunk in synth:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    synth_parts.append(delta.content)
                    yield {"type": "token", "token": delta.content}

        yield from run_with_retry(
            _synth_attempt, model=resolved,
            has_partial_output=lambda: bool(synth_parts))
        turn_had_text = turn_had_text or bool(synth_parts)
        if not synth_parts:
            yield from _emit_silent_end(chat_id, resolved, "synthesis_empty",
                                        "rounds" if exhausted else "synthesis",
                                        max_iters, last_tool_failure)
            turn_had_text = True

    if not turn_had_text:
        yield from _emit_silent_end(chat_id, resolved, "turn_ended_without_text", "empty",
                                    max_iters, last_tool_failure, finish=finish_reason)


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
