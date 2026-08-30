"""Normalized per-turn tool-exchange transcript ↔ provider wire shapes.

A turn's transcript is a JSON list of entries in a provider-agnostic shape
(chats can switch providers per turn, so no provider's wire format is stored):

  {"role": "assistant", "text": "...", "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool", "id": "...", "name": "...", "text": "<result text>"}

agent.py emits these as `transcript_append` events at the exact points it
appends to its own in-turn message list; chat_turn.py accumulates them and
persists via store.chat_turn_persist (truncated by truncate_for_persist).
On later turns, rows_to_provider_messages replays the stored exchange in
whichever wire shape the current provider needs — giving the model memory of
prior tool calls/results it never had before.
"""
from config import TRANSCRIPT_TOOL_RESULT_CHARS


def truncate_for_persist(transcript):
    """Cap each tool result's text for long-term storage. The live turn already
    saw the full text; this bounds what every future turn re-reads."""
    out = []
    for entry in transcript or []:
        if entry.get("role") == "tool":
            text = entry.get("text") or ""
            if len(text) > TRANSCRIPT_TOOL_RESULT_CHARS:
                text = text[:TRANSCRIPT_TOOL_RESULT_CHARS] + "\n…(truncated for history)"
            entry = {**entry, "text": text}
        out.append(entry)
    return out


def entry_to_openai_messages(entry):
    if entry["role"] == "assistant":
        return [{
            "role": "assistant",
            "content": entry.get("text") or None,
            "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": _args_json(tc)}}
                for tc in entry.get("tool_calls") or []
            ],
        }]
    return [{"role": "tool", "tool_call_id": entry["id"], "content": entry.get("text") or ""}]


def entry_to_anthropic_messages(entry):
    if entry["role"] == "assistant":
        content = []
        if entry.get("text"):
            content.append({"type": "text", "text": entry["text"]})
        for tc in entry.get("tool_calls") or []:
            content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"],
                            "input": tc.get("args") or {}})
        return [{"role": "assistant", "content": content}]
    return [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": entry["id"],
                                          "content": entry.get("text") or ""}]}]


def _args_json(tc):
    import json
    args = tc.get("args")
    return args if isinstance(args, str) else json.dumps(args or {})


def rows_to_provider_messages(rows, provider):
    """Replay stored turn rows as provider-ready messages (no system message).

    rows: [{role, content, model_transcript}] oldest-first, from
    store.chat_turn_persist.load_turn_rows. Rows persisted before the
    model_transcript column (or by the non-agentic flows) have transcript None
    and replay exactly as before: their display text only.
    """
    convert = (entry_to_anthropic_messages if provider == "anthropic"
               else entry_to_openai_messages)
    out = []
    for row in rows:
        role = row.get("role")
        if role == "user":
            out.append({"role": "user", "content": row.get("content") or ""})
        elif role == "assistant":
            transcript = row.get("model_transcript") or []
            for entry in transcript:
                out.extend(convert(entry))
            text = (row.get("content") or "").strip()
            if text or not transcript:
                out.append({"role": "assistant", "content": row.get("content") or ""})
    return out
