"""Agent-level retry for transient LLM-provider errors.

Previously any exception mid-turn killed the whole turn (and, before the
durable-persistence work, everything the user typed). run_with_retry wraps a
provider call + stream-drain closure: transient failures back off and retry
(2s → 4s → 8s at the defaults), terminal ones re-raise immediately, and a
failure AFTER any output already streamed is never retried — retrying would
duplicate content the client already rendered; the route's error handler and
durable partial-persistence take over instead.
"""
import logging
import time

import anthropic
import openai

import config
from helpers.llm_helpers import _TRANSIENT_MARKERS

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}

_RETRYABLE_TYPES = (
    openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError,
    anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError,
)
_TERMINAL_TYPES = (
    openai.AuthenticationError, openai.PermissionDeniedError,
    openai.BadRequestError, openai.NotFoundError,
    anthropic.AuthenticationError, anthropic.PermissionDeniedError,
    anthropic.BadRequestError, anthropic.NotFoundError,
)


def classify_llm_error(exc) -> str:
    """'retryable' or 'terminal'. Typed SDK exceptions first (both SDKs share
    an APIStatusError with .status_code); the string-marker fallback covers
    NRP's occasionally-untyped proxy errors. Unknown classes fail fast."""
    if isinstance(exc, _RETRYABLE_TYPES):
        return "retryable"
    if isinstance(exc, _TERMINAL_TYPES):
        return "terminal"
    if getattr(exc, "status_code", None) in _RETRYABLE_STATUS:
        return "retryable"
    if any(m in str(exc).lower() for m in _TRANSIENT_MARKERS):
        return "retryable"
    return "terminal"


def run_with_retry(attempt_factory, *, model, has_partial_output,
                   max_attempts=None, base_delay_ms=None, sleep=None):
    """Generator: drive a fresh attempt generator (which opens the provider
    call and streams events live — token yields pass straight through, and it
    resets its own accumulators at the top so a re-attempt starts clean) up to
    max_attempts times, yielding step_start/step_done retry events between
    attempts. has_partial_output() must report CLIENT-VISIBLE output only
    (tokens already yielded downstream): when True, ANY retry is refused
    regardless of classification — retrying would duplicate rendered content."""
    attempts = max_attempts or config.LLM_RETRY_MAX
    delay_ms = base_delay_ms if base_delay_ms is not None else config.LLM_RETRY_BASE_DELAY_MS
    for attempt in range(1, attempts + 1):
        try:
            yield from attempt_factory()
            return
        except Exception as exc:
            if has_partial_output() or classify_llm_error(exc) == "terminal" or attempt == attempts:
                raise
            wait_s = (delay_ms / 1000.0) * (2 ** (attempt - 1))
            logger.warning("[llm_retry] %s attempt %d/%d failed (%s), retrying in %.1fs",
                           model, attempt, attempts, exc.__class__.__name__, wait_s)
            yield {"type": "step_start", "name": "retry",
                   "label": f"{model} hiccup — retrying (attempt {attempt}/{attempts})…"}
            (sleep or time.sleep)(wait_s)  # late-bound so tests can patch time.sleep
            yield {"type": "step_done", "name": "retry", "label": "Retrying now"}
