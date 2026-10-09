"""What one chat request is made of, for the composer's context bar
(frontend js/context_bar.js).

agent.py yields {"type": "context_usage", "usage": measure(...)} after each
LLM call; chat_turn.py forwards it as an SSE event and saves the turn's last
one on the chat. The parts are estimated from characters (CHARS_PER_TOKEN)
and, when the provider reports its own input-token count, scaled to it
(`measured`).

  system        the chat's system prompt
  tools         the tool schemas offered on that call
  studies       the study context: pinned reports, the project's studies
  summary       the compacted earlier conversation
  conversation  earlier turns replayed verbatim, with their tool exchanges
  turn          this message (with any slash hint) and this turn's tool calls/results
"""
import json

import config
from config import ALLOWED_MODELS, MODEL_METADATA


def _block_chars(block):
    if block.get("type") == "tool_use":
        return len(json.dumps(block.get("input") or {}))
    text = block.get("text") if "text" in block else block.get("content")
    return len(text) if isinstance(text, str) else len(json.dumps(text or ""))


def msg_chars(m):
    """Characters of one message, in either provider's wire shape."""
    content = m.get("content")
    n = len(content) if isinstance(content, str) else sum(_block_chars(b) for b in content or [])
    for tc in m.get("tool_calls") or []:
        n += len(tc["function"]["name"]) + len(tc["function"].get("arguments") or "")
    return n


def request_chars(msgs, tools):
    return sum(msg_chars(m) for m in msgs) + (len(json.dumps(tools)) if tools else 0)


def _window(model):
    return (MODEL_METADATA.get(model) or {}).get("context") or 0


def measure(msgs, layout, tools, model, reported=None):
    """Token counts per part of the request msgs (a helpers.context_fit.Layout
    says which messages are which)."""
    chars = {
        "system":       len(layout.system_prompt or ""),
        "tools":        len(json.dumps(tools)) if tools else 0,
        "studies":      len(layout.study_ctx or ""),
        "summary":      len(layout.summary or ""),
        "conversation": sum(msg_chars(m) for m in msgs[1:layout.history_end]),
        "turn":         sum(msg_chars(m) for m in msgs[layout.history_end:]),
    }
    estimate = sum(chars.values()) / config.CHARS_PER_TOKEN
    scale = reported / estimate if reported and estimate else 1
    parts = {k: round(v / config.CHARS_PER_TOKEN * scale) for k, v in chars.items()}
    window = _window(model)
    return {
        "model": model,
        "window": window,
        "compact_at": max(0, window - 8_000 - config.HISTORY_COMPACTION_RESERVE_TOKENS),
        "total": reported or sum(parts.values()),
        "parts": parts,
        "measured": bool(reported),
        "windows": {m: _window(m) for m in ALLOWED_MODELS},
    }
