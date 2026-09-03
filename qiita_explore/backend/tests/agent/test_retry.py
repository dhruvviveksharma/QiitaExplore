"""Retry on transient LLM errors: exponential backoff with SSE retry steps,
fail-fast on terminal errors, and a hard no-retry rule once any output has
reached the client.
"""
import openai

from helpers.llm_retry import classify_llm_error, run_with_retry

from .fakes import (
    FakeChunk, FakeChoice, FakeDelta,
    openai_text_round, openai_tool_call_round,
    make_fake_execute_tool, tool_result, tokens_of, events_of_type,
)


class _FakeTransientError(Exception):
    """Untyped proxy-style error carrying a transient marker string."""
    def __str__(self):
        return "upstream connect error or disconnect/reset before headers"


class _FakeTerminalError(Exception):
    def __str__(self):
        return "model exploded in a novel, unclassifiable way"


class TestClassification:

    def test_transient_marker_string_is_retryable(self):
        assert classify_llm_error(_FakeTransientError()) == "retryable"

    def test_status_code_529_is_retryable(self):
        exc = Exception("overloaded")
        exc.status_code = 529
        assert classify_llm_error(exc) == "retryable"

    def test_unknown_error_is_terminal(self):
        assert classify_llm_error(_FakeTerminalError()) == "terminal"

    def test_typed_bad_request_is_terminal(self):
        import httpx
        req = httpx.Request("POST", "http://x")
        resp = httpx.Response(400, request=req, json={"error": "context_length_exceeded"})
        exc = openai.BadRequestError("too long", response=resp, body=None)
        assert classify_llm_error(exc) == "terminal"


class TestRunWithRetry:

    def test_retry_then_success_emits_step_events(self):
        calls = {"n": 0}
        out = []

        def factory():
            calls["n"] += 1
            if calls["n"] == 1:
                raise _FakeTransientError()
            yield {"type": "token", "token": "ok"}
            out.append("done")

        slept = []
        events = list(run_with_retry(factory, model="m", has_partial_output=lambda: False,
                                     max_attempts=3, base_delay_ms=10, sleep=slept.append))
        assert calls["n"] == 2 and out == ["done"]
        assert [e["type"] for e in events] == ["step_start", "step_done", "token"]
        assert "attempt 1/3" in events[0]["label"]
        assert slept == [0.01]

    def test_backoff_doubles_per_attempt(self):
        def factory():
            raise _FakeTransientError()
            yield  # pragma: no cover

        slept = []
        try:
            list(run_with_retry(factory, model="m", has_partial_output=lambda: False,
                                max_attempts=3, base_delay_ms=1000, sleep=slept.append))
        except _FakeTransientError:
            pass
        else:  # pragma: no cover
            raise AssertionError("should exhaust and re-raise")
        assert slept == [1.0, 2.0]  # 2 retries between 3 attempts

    def test_terminal_error_fails_fast(self):
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            raise _FakeTerminalError()
            yield  # pragma: no cover

        try:
            list(run_with_retry(factory, model="m", has_partial_output=lambda: False,
                                max_attempts=3, base_delay_ms=1, sleep=lambda s: None))
        except _FakeTerminalError:
            pass
        assert calls["n"] == 1

    def test_no_retry_after_partial_output(self):
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            yield {"type": "token", "token": "already rendered"}
            raise _FakeTransientError()

        try:
            list(run_with_retry(factory, model="m", has_partial_output=lambda: True,
                                max_attempts=3, base_delay_ms=1, sleep=lambda s: None))
        except _FakeTransientError:
            pass
        assert calls["n"] == 1


class TestRetryInTheLoop:

    def test_transient_connect_error_then_success(self, run_turn, monkeypatch):
        import helpers.llm_retry as retry_mod
        monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)
        script = [
            _FakeTransientError(),                # attempt 1: raises on create()
            openai_text_round("recovered fine"),  # attempt 2: streams
        ]
        events, client, _ = run_turn(script, make_fake_execute_tool())
        assert tokens_of(events) == "recovered fine"
        retry_steps = [e for e in events_of_type(events, "step_start") if e["name"] == "retry"]
        assert len(retry_steps) == 1
        assert len(client.calls) == 2

    def test_retry_between_tool_rounds(self, run_turn, monkeypatch):
        """A transient failure opening round 2 (after round 1's tool ran and
        nothing of round 2 reached the client) retries cleanly."""
        import helpers.llm_retry as retry_mod
        monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)
        script = [
            openai_tool_call_round("c1", "search_studies", '{"keywords": ["a"]}'),
            _FakeTransientError(),
            openai_text_round("after the blip"),
        ]
        events, client, tool = run_turn(script, make_fake_execute_tool(tool_result()))
        assert tokens_of(events) == "after the blip"
        assert len(tool.calls) == 1          # tool ran exactly once
        assert len(client.calls) == 3        # round1 + failed round2 + retried round2

    def test_mid_stream_failure_after_tokens_is_not_retried(self, run_turn, monkeypatch):
        import helpers.llm_retry as retry_mod
        monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)

        class _ExplodingIter:
            def __init__(self):
                self._sent = False
            def __iter__(self):
                return self
            def __next__(self):
                if not self._sent:
                    self._sent = True
                    return FakeChunk([FakeChoice(FakeDelta(content="partial"))])
                raise _FakeTransientError()

        script = [[FakeChunk([FakeChoice(FakeDelta(content="partial"))])]]

        # hand-build a client whose stream explodes after one token
        from .fakes import FakeOpenAIClient
        client = FakeOpenAIClient(script)
        client.chat.completions.create = lambda **kw: _ExplodingIter()

        import helpers.agent as agent_mod
        monkeypatch.setattr(agent_mod, "get_client", lambda m: (client, "nrp"))
        monkeypatch.setattr(agent_mod, "execute_tool", make_fake_execute_tool())

        gen = agent_mod.stream_agent(
            [{"role": "user", "content": "hi"}], system_prompt="sp", model="minimax-m2",
            study_context_text=None, scope="global", chat_id="c",
            tools=[])
        collected = []
        try:
            for ev in gen:
                collected.append(ev)
        except _FakeTransientError:
            pass
        else:  # pragma: no cover
            raise AssertionError("mid-stream failure after output must propagate")
        assert tokens_of(collected) == "partial"
