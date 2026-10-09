"""A request the provider rejects as too long, through the real agent loop:
compacted and retried once (helpers/context_fit.py), never twice, and every
LLM call reports what it held for the context bar (context_usage events).
"""
import copy
from types import SimpleNamespace

import anthropic
import openai
import pytest

import config
import helpers.chat_history as ch
from tests.test_context_fit import CLAUDE, _rows, anthropic_error, openai_error

from .fakes import (
    FakeAnthEvent, FakeAnthropicClient, FakeChunk, FakeOpenAIClient, anthropic_text_round,
    events_of_type, make_fake_execute_tool, openai_text_round, openai_tool_call_round,
    tokens_of, tool_result,
)

TOOLS = [{"type": "function", "function": {"name": "search_studies", "parameters": {}}}]
OVERFLOW = openai_error("context_length_exceeded")   # no counts: the model's window, 2.5 chars/token
TRIM = "…(trimmed to fit the context window)"


@pytest.fixture
def summarizer(monkeypatch):
    persisted = []
    monkeypatch.setattr(ch, "llm_chat", lambda *a, **kw: "NEW SUMMARY")
    monkeypatch.setattr(ch, "persist_compaction_state", lambda *a, **kw: persisted.append(kw))
    return persisted


@pytest.fixture
def run(agent_mod, monkeypatch):
    """One stream_agent turn on the route path (stored rows replayed) against
    a fake client that copies each call's arguments when made — the loop
    keeps changing its message list afterwards."""
    def _run(script, *, provider="nrp", execute=None, rows=None, model=None, **extra):
        client = FakeAnthropicClient(script) if provider == "anthropic" else FakeOpenAIClient(script)
        client.snaps = []
        target = client.messages if provider == "anthropic" else client.chat.completions
        name = "stream" if provider == "anthropic" else "create"
        orig = getattr(target, name)

        def snap(**kw):
            client.snaps.append(copy.deepcopy(kw))
            return orig(**kw)

        setattr(target, name, snap)
        monkeypatch.setattr(agent_mod, "get_client", lambda m: (client, provider))
        monkeypatch.setattr(agent_mod, "execute_tool", execute or make_fake_execute_tool())
        kwargs = dict(system_prompt="sp", study_context_text="S" * 1_000, scope="global", chat_id="c1",
                      model=model or ("claude-haiku-4-5" if provider == "anthropic" else "minimax-m2"),
                      tools=TOOLS, turn_rows=_rows(4) if rows is None else rows, user_content="and now?")
        kwargs.update(extra)
        events = []
        try:
            for e in agent_mod.stream_agent([], **kwargs):
                events.append(e)
        except Exception as exc:
            return events, client, exc
        return events, client, None
    return _run


def _steps(events, name="context_fit"):
    return [e for e in events if e["type"] in ("step_start", "step_done") and e["name"] == name]


class TestRetry:

    def test_overflow_compacts_and_retries_once(self, run, summarizer):
        events, client, exc = run([OVERFLOW, openai_text_round("ok")])
        assert exc is None and tokens_of(events) == "ok"
        assert [e["type"] for e in _steps(events)] == ["step_start", "step_done"]
        first, second = client.snaps[0]["messages"], client.snaps[1]["messages"]
        assert "NEW SUMMARY" in second[0]["content"] and "NEW SUMMARY" not in first[0]["content"]
        assert len(second) < len(first) and second[-1]["content"] == "and now?"
        assert summarizer and summarizer[0]["through_id"] == 6

    def test_overflow_after_a_tool_round_keeps_this_turns_exchange(self, run, summarizer):
        script = [openai_tool_call_round("c1", "search_studies", '{"keywords": ["x"]}'),
                  openai_error("This model's maximum context length is 20000 tokens. "
                               "However, you requested 100000 tokens."),
                  openai_text_round("done")]
        events, client, exc = run(script, execute=make_fake_execute_tool(tool_result(text="R" * 30_000)))
        assert exc is None and tokens_of(events) == "done"
        retry = client.snaps[2]["messages"]
        assert retry[-2]["tool_calls"][0]["id"] == "c1"
        assert retry[-1]["role"] == "tool" and retry[-1]["content"].endswith(TRIM)
        assert "study context trimmed" in _steps(events)[1]["detail"]

    def test_a_second_overflow_propagates_with_its_size(self, run, summarizer):
        tight = openai_error("This model's maximum context length is 1000 tokens. However, you requested 9000 tokens.")
        events, client, exc = run([OVERFLOW, tight])
        assert isinstance(exc, openai.BadRequestError) and len(client.snaps) == 2
        last = events_of_type(events, "context_usage")[-1]["usage"]
        assert last["measured"] and last["total"] == 9000

    def test_other_bad_requests_are_not_compacted(self, run, summarizer):
        bad = openai_error("Invalid schema for function 'search_studies'")
        events, client, exc = run([bad, bad])
        assert isinstance(exc, openai.BadRequestError)
        assert _steps(events) == [] and summarizer == []
        # one plain retry without stream_options (a server that rejects it), then the error
        assert "stream_options" in client.snaps[0] and "stream_options" not in client.snaps[1]

    def test_forced_round_overflow_is_not_sent_twice(self, run, summarizer):
        force = {"name": "search_studies", "args": {"keywords": ["x"]}, "text": ""}
        script = [OVERFLOW, openai_tool_call_round("c1", "search_studies", "{}"), openai_text_round("done")]
        events, client, exc = run(script, execute=make_fake_execute_tool(tool_result()), force_tool=force)
        assert exc is None and len(client.snaps) == 3
        assert client.snaps[0]["tool_choice"] == client.snaps[1]["tool_choice"] == "required"

    def test_anthropic(self, run, summarizer):
        started = FakeAnthEvent("message_start", message=SimpleNamespace(usage=SimpleNamespace(input_tokens=777)))
        exc_in = anthropic_error(CLAUDE)
        events, client, exc = run([exc_in, [started] + anthropic_text_round("ok")], provider="anthropic")
        assert exc is None and tokens_of(events) == "ok"
        assert "NEW SUMMARY" in client.snaps[1]["system"] and "NEW SUMMARY" not in client.snaps[0]["system"]
        assert client.snaps[1]["messages"][-1]["content"] == "and now?"
        usage = events_of_type(events, "context_usage")[-1]["usage"]
        assert usage["measured"] and usage["total"] == 777

    def test_anthropic_second_overflow_raises(self, run, summarizer):
        events, client, exc = run([anthropic_error(CLAUDE), anthropic_error(CLAUDE)], provider="anthropic")
        assert isinstance(exc, anthropic.BadRequestError) and len(client.snaps) == 2


class TestUsage:

    def test_each_call_reports_its_parts(self, run):
        usage_chunk = FakeChunk([])
        usage_chunk.usage = SimpleNamespace(prompt_tokens=4321)
        events, client, exc = run([openai_text_round("ok") + [usage_chunk]], rows=[])
        assert client.snaps[0]["stream_options"] == {"include_usage": True}
        (ev,) = events_of_type(events, "context_usage")
        u = ev["usage"]
        assert u["measured"] and u["total"] == 4321 and u["model"] == "minimax-m2"
        assert set(u["parts"]) == {"system", "tools", "studies", "summary", "conversation", "turn"}
        assert u["parts"]["studies"] > u["parts"]["turn"] > 0 and u["parts"]["conversation"] == 0
        assert u["window"] == config.MODEL_METADATA["minimax-m2"]["context"]
        assert u["windows"]["claude-haiku-4-5"] == 200_000

    def test_estimated_when_the_model_reports_nothing(self, run, monkeypatch):
        monkeypatch.setitem(config.MODEL_METADATA, "minimax-m2",
                            {**config.MODEL_METADATA["minimax-m2"], "stream_usage": False})
        events, client, exc = run([openai_text_round("ok")])
        assert "stream_options" not in client.snaps[0]
        u = events_of_type(events, "context_usage")[0]["usage"]
        assert not u["measured"] and u["parts"]["conversation"] > 0
        assert u["total"] == sum(u["parts"].values())
