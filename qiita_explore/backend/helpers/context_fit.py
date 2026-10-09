"""Recovering a turn whose request was too long for the model.

Between turns, chat_history.prepare_history keeps the replayed history inside
the model's budget by estimate (chars ÷ CHARS_PER_TOKEN). Inside a turn
nothing shrinks, so one request can still overflow: pinned reports in the
system message, this turn's own big tool results, or text that takes more
tokens than the estimate. The provider rejects it before anything streams, and
OverflowGuard (wrapped around each LLM call in agent.py) runs recover() once,
then calls again:

  1. autocompact: the replayed turns are summarized as between turns (same
     summarizer, persisted, so the next turn starts from it too), keeping a
     smaller verbatim tail;
  2. if the request is still over target, this turn's tool results are cut;
  3. then the study context in the system message.

The target leaves room for the reply below the window, in real tokens: the
rejection usually states the counts, which give this request's own
chars-per-token ratio. A second rejection propagates, and
llm_helpers.friendly_llm_error turns it into too_long_message.
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional

import config
from config import MODEL_METADATA
from helpers.chat_history import split_turns, summarize_turns
from helpers.chat_transcript import rows_to_provider_messages
from helpers.context_usage import measure, request_chars
from helpers.llm_helpers import _build_api_messages, is_context_overflow
from helpers.turn_log import log_turn_event

logger = logging.getLogger(__name__)

_SUMMARY_HEAD = "\n\nEARLIER CONVERSATION (compacted summary):\n"
_OMITTED_NOTE = "[Earlier conversation omitted to fit the context window.]"
_TRIM_NOTE = "\n…(trimmed to fit the context window)"
_STUDY_TRIM_NOTE = "\n[study context trimmed to fit the context window]"
_FALLBACK_CHARS_PER_TOKEN = 2.5   # when the rejection states no counts: assume dense text

# (sent, limit) from the rejection text, provider by provider.
_COUNT_PATTERNS = (
    # vLLM / OpenAI: "maximum context length is 131072 tokens. However, you requested 150000 tokens"
    (re.compile(r"maximum context length is (\d+) tokens.*?(?:requested|resulted in) (\d+) tokens", re.S), True),
    # Anthropic: "prompt is too long: 215000 tokens > 200000 maximum"
    (re.compile(r"(\d+) tokens > (\d+) maximum"), False),
    # newer vLLM: "(length 140000) is longer than the maximum model length of 131072"
    (re.compile(r"length (\d+)\) is longer than the maximum model length of (\d+)"), False),
)


@dataclass
class Layout:
    """What stream_agent's message list is made of: msgs[0] is the system
    message built from these parts, msgs[1:history_end] the replayed earlier
    turns, msgs[history_end] this turn's user message, then this turn's tool
    exchange. turn_rows is None on the harness path (no stored chat)."""
    system_prompt: str
    study_ctx: Optional[str]
    summary: Optional[str]
    turn_rows: Optional[list]
    provider: str
    history_end: int = 1


def system_message(layout):
    msg = _build_api_messages([], layout.study_ctx, layout.system_prompt)[0]
    if layout.summary:
        msg = {**msg, "content": msg["content"] + _SUMMARY_HEAD + layout.summary}
    return msg


def overflow_tokens(exc):
    """(sent_tokens, limit_tokens) when the rejection states them, else None."""
    text = str(exc)
    for pattern, limit_first in _COUNT_PATTERNS:
        m = pattern.search(text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return (b, a) if limit_first else (a, b)
    return None


def _cut_text(text):
    if len(text) <= config.TRANSCRIPT_TOOL_RESULT_CHARS:
        return text, False
    return text[:config.TRANSCRIPT_TOOL_RESULT_CHARS] + _TRIM_NOTE, True


def _trim_tool_results(msgs, start):
    """Stage 2: cut this turn's tool results (both wire shapes) in place."""
    n = 0
    for i in range(start, len(msgs)):
        m = msgs[i]
        if m.get("role") == "tool" and isinstance(m.get("content"), str):
            text, cut = _cut_text(m["content"])
            if cut:
                msgs[i], n = {**m, "content": text}, n + 1
        elif m.get("role") == "user" and isinstance(m.get("content"), list):
            blocks = []
            for b in m["content"]:
                if b.get("type") == "tool_result" and isinstance(b.get("content"), str):
                    text, cut = _cut_text(b["content"])
                    if cut:
                        b, n = {**b, "content": text}, n + 1
                blocks.append(b)
            msgs[i] = {**m, "content": blocks}
    return n


def _compact_history(msgs, layout, *, model, chat_id, scope):
    """Stage 1: summarize the replayed turns into the rolling summary (kept
    across turns) and rebuild msgs around it. Returns the step detail."""
    if layout.history_end <= 1:
        return None
    current = msgs[layout.history_end:]
    history, detail = [], "earlier messages left out"
    try:
        if layout.turn_rows is None:
            raise LookupError("no stored chat to summarize")
        older, kept = split_turns(layout.turn_rows, config.HISTORY_KEEP_VERBATIM_TOKENS // 4, min_keep=0)
        if older:
            layout.summary = summarize_turns(chat_id, scope, model, older, layout.summary)
            detail = f"{len(older)} earlier turn{'s' if len(older) != 1 else ''} summarized"
        else:
            detail = None
        layout.turn_rows = [r for turn in kept for r in turn]
        history = rows_to_provider_messages(layout.turn_rows, layout.provider)
    except Exception as exc:
        if not isinstance(exc, LookupError):
            logger.exception("[context_fit] compaction failed for chat %s", chat_id)
            log_turn_event(chat_id, "compaction_failed", model=model, exc=exc.__class__.__name__)
        layout.summary = f"{layout.summary}\n\n{_OMITTED_NOTE}" if layout.summary else _OMITTED_NOTE
        layout.turn_rows = []
    msgs[:] = [system_message(layout)] + history + current
    layout.history_end = 1 + len(history)
    return detail


def recover(msgs, layout, *, exc, model, chat_id, scope, tools=None):
    """Generator: rewrite msgs (and layout) in place so the retry fits,
    yielding step events. Each stage runs only while still over target."""
    counts = overflow_tokens(exc)
    sent_chars = request_chars(msgs, tools)
    if counts:
        sent_tokens, limit = counts
        ratio = sent_chars / max(sent_tokens, 1)
    else:
        limit = (MODEL_METADATA.get(model) or {}).get("context") or 128_000
        ratio = _FALLBACK_CHARS_PER_TOKEN
    target = int((limit - max(limit // 10, 8_000)) * ratio)   # leave room for the reply
    log_turn_event(chat_id, "context_overflow_retry", model=model, chars=sent_chars,
                   sent_tokens=counts and counts[0], limit=limit)
    yield {"type": "step_start", "name": "context_fit",
           "label": f"Context full for {model} — compacting the conversation…"}

    details = [_compact_history(msgs, layout, model=model, chat_id=chat_id, scope=scope)]
    if request_chars(msgs, tools) > target:
        n = _trim_tool_results(msgs, layout.history_end)
        details.append(n and f"{n} tool result{'s' if n != 1 else ''} trimmed")
    over = request_chars(msgs, tools) - target
    if over > 0 and layout.study_ctx:
        layout.study_ctx = layout.study_ctx[:max(0, len(layout.study_ctx) - over)] + _STUDY_TRIM_NOTE
        msgs[0] = system_message(layout)
        details.append("study context trimmed")
    yield {"type": "step_done", "name": "context_fit", "label": "Conversation compacted",
           "detail": " · ".join(d for d in details if d) or "trimmed to fit"}


class OverflowGuard:
    """One recover-and-retry per turn, around each LLM call of agent.py's
    loops. msgs is the loop's own message list (rewritten in place)."""

    def __init__(self, msgs, layout, *, model, chat_id, scope):
        self.msgs, self.layout = msgs, layout
        self.model, self.chat_id, self.scope = model, chat_id, scope
        self.used = False

    def run(self, call, tools=None):
        """yield from call(); on the turn's first overflow, recover and call
        once more. When it still doesn't fit, the context bar gets the
        rejected request's size before the error propagates."""
        while True:
            try:
                yield from call()
                return
            except Exception as exc:
                if not is_context_overflow(exc):
                    raise
                if self.used:
                    sent = (overflow_tokens(exc) or (None,))[0]
                    yield {"type": "context_usage",
                           "usage": measure(self.msgs, self.layout, tools, self.model, reported=sent)}
                    raise
                self.used = True
                yield from recover(self.msgs, self.layout, exc=exc, model=self.model,
                                   chat_id=self.chat_id, scope=self.scope, tools=tools)
