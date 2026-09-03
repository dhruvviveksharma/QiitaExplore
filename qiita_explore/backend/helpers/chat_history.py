"""Budget-aware history preparation for the agentic turn, with compaction.

prepare_history() is a generator (so it can surface step events while a
summarization call runs) that returns, via `yield from`:

    (turn_rows, summary)

- turn_rows: the rows stream_agent should replay (everything after the
  compaction anchor).
- summary: the rolling summary of everything before the anchor (None until
  a chat grows big enough to compact) — stream_agent folds it into the
  system message.

Trigger math reuses the app's existing budget seam: history may spend
context_budget_chars(model) minus the fixed system+context text minus a
reserve for the turn's own live growth (this turn's tool rounds haven't
happened yet when the decision is made). Compaction summarizes whole TURNS
(a user row plus its assistant rows), never splitting a tool_call from its
tool_result — free here because the transcript lives on the assistant row.
Repeated compactions re-anchor: already-summarized turns are never
re-summarized (the previous summary is folded into the next one).
"""
import json
import logging

import config
from config import context_budget_chars
from helpers.llm_helpers import llm_chat
from store.chat_turn_persist import (
    get_compaction_state, load_turn_rows, persist_compaction_state,
)

logger = logging.getLogger(__name__)

_COMPACTION_SYSTEM_PROMPT = (
    "You compress chat history. Summarize the conversation below into a dense "
    "briefing a model can continue from. Keep, as explicit sections: the user's "
    "goals; any pinned or repeatedly-referenced study IDs (verbatim); key facts "
    "and tool findings (study IDs, counts, names); decisions made; and open "
    "questions. Do not add commentary or advice — output only the summary."
)

_COMPACTION_REQUEST = "Summarize the conversation above per your instructions."


def _row_chars(row):
    n = len(row.get("content") or "")
    transcript = row.get("model_transcript")
    if transcript:
        n += len(json.dumps(transcript))
    return n


def _pair_rows_into_turns(rows):
    """Group rows into turns: each turn starts at a user row and carries every
    following row until the next user row. Leading assistant rows (legacy
    oddities) form their own head turn."""
    turns = []
    current = []
    for row in rows:
        if row.get("role") == "user" and current:
            turns.append(current)
            current = []
        current.append(row)
    if current:
        turns.append(current)
    return turns


def _serialize_turns(turns):
    """Plain-text rendering of turns for the summarization call."""
    lines = []
    for turn in turns:
        for row in turn:
            if row.get("role") == "user":
                lines.append(f"[User]: {row.get('content') or ''}")
            else:
                for entry in row.get("model_transcript") or []:
                    if entry.get("role") == "assistant" and entry.get("tool_calls"):
                        calls = ", ".join(f"{tc['name']}({json.dumps(tc.get('args') or {})})"
                                          for tc in entry["tool_calls"])
                        lines.append(f"[Assistant tool calls]: {calls}")
                    elif entry.get("role") == "tool":
                        lines.append(f"[Tool result {entry.get('name')}]: {entry.get('text') or ''}")
                lines.append(f"[Assistant]: {row.get('content') or ''}")
    return "\n".join(lines)


def _history_budget_chars(model, system_prompt, context_block):
    reserve_chars = int(config.HISTORY_COMPACTION_RESERVE_TOKENS * config.CHARS_PER_TOKEN)
    fixed_chars   = len(system_prompt or "") + len(context_block or "")
    return max(8_000, context_budget_chars(model) - fixed_chars - reserve_chars)


def prepare_history(chat_id, scope, model, system_prompt, context_block, until_id=None):
    """Generator — yields step events while compacting; returns
    (turn_rows, summary) via StopIteration for `yield from` callers.
    until_id excludes the current turn's just-persisted user row."""
    state = get_compaction_state(chat_id, scope)
    rows  = load_turn_rows(chat_id, scope, since_id=state["through_id"], until_id=until_id)
    summary = state["summary"]

    budget = _history_budget_chars(model, system_prompt, context_block)
    total  = sum(_row_chars(r) for r in rows) + len(summary or "")
    if total <= budget:
        return rows, summary

    turns = _pair_rows_into_turns(rows)
    keep_budget = int(config.HISTORY_KEEP_VERBATIM_TOKENS * config.CHARS_PER_TOKEN)
    kept, older, running = [], [], 0
    for turn in reversed(turns):
        turn_chars = sum(_row_chars(r) for r in turn)
        if running + turn_chars > keep_budget and kept:
            older.append(turn)
        else:
            kept.append(turn)
            running += turn_chars
    kept.reverse()
    older.reverse()

    if not older:
        # Pathological: the keep window alone exceeds budget (one giant turn).
        # Ship it and let the provider's own limit surface if truly too big.
        logger.warning("[compaction] %s chat %s over budget but nothing to compact",
                       scope, chat_id)
        return rows, summary

    yield {"type": "step_start", "name": "compaction",
           "label": "Compacting conversation history…"}
    serialized = _serialize_turns(older)
    if summary:
        serialized = f"[Summary of even earlier conversation]:\n{summary}\n\n{serialized}"
    new_summary = llm_chat(
        [{"role": "user", "content": serialized},
         {"role": "user", "content": _COMPACTION_REQUEST}],
        study_context_text=None, system_prompt=_COMPACTION_SYSTEM_PROMPT, model=model,
    )
    new_through_id = older[-1][-1]["id"]
    persist_compaction_state(chat_id, scope, summary=new_summary, through_id=new_through_id)
    logger.info("[compaction] %s chat %s: %d turns summarized through row %d",
                scope, chat_id, len(older), new_through_id)
    yield {"type": "step_done", "name": "compaction", "label": "History compacted",
           "detail": f"{len(older)} earlier turns summarized"}

    kept_rows = [r for turn in kept for r in turn]
    return kept_rows, new_summary
