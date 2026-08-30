"""Forward specs for the Phase 3b retry module (helpers/llm_retry.py).

Skipped until run_with_retry/classify_llm_error exist — kept here so the
architecture and test workstreams don't diverge on assumed shapes.
"""
import pytest

pytestmark = pytest.mark.skip(reason="pending Phase 3b: helpers/llm_retry.py")


def test_retry_on_429_then_success():
    """Transient error on attempt 1 → retry step events → attempt 2 streams
    normally; total sleep follows LLM_RETRY_BASE_DELAY_MS backoff."""


def test_terminal_error_fails_fast():
    """A BadRequest/auth error raises immediately with zero retry attempts."""


def test_no_retry_after_partial_output():
    """An exception after any content streamed re-raises regardless of
    classification — retrying would duplicate output."""
