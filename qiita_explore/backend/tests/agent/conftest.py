"""Fixtures for loop-level agent tests: the REAL helpers.agent module with its
provider client and tool boundary swapped for scripted fakes.

Unlike tests/test_agent_tool_call.py (which stubs config/llm_helpers via
sys.modules to unit-test _execute_tool_call in isolation), these fixtures
import everything for real — the autouse fresh_db fixture in tests/conftest.py
has already stubbed qiita_db/qiita_core and pointed SQLite at a temp file —
and only monkeypatch two attributes on helpers.agent per test.
"""
import sys

import pytest

from tests.conftest import stub_qiita_db_and_core

# .fakes imports helpers.agent_tools (for the real ToolResult), whose import
# chain reaches the vendored qiita_core — stub before that import happens,
# since collection runs before the autouse fresh_db fixture does.
stub_qiita_db_and_core()

from .fakes import FakeAnthropicClient, FakeOpenAIClient  # noqa: E402


@pytest.fixture
def agent_mod():
    # test_agent_tool_call.py's module-scoped stub fixture pops helpers.agent
    # on teardown, but if this test runs after it in the same session a stale
    # stub-bound module could linger — force a clean import against the real
    # config/llm_helpers/agent_tools.
    sys.modules.pop("helpers.agent", None)
    import helpers.agent as agent
    return agent


@pytest.fixture
def run_turn(agent_mod, monkeypatch):
    """Run one stream_agent turn against scripted fakes; returns
    (events, fake_client, fake_execute_tool)."""
    def _run(script, execute_tool, *, provider="nrp", tools=None, messages=None,
             max_iters=None, search_budget=None):
        client = (FakeAnthropicClient(script) if provider == "anthropic"
                  else FakeOpenAIClient(script))
        monkeypatch.setattr(agent_mod, "get_client", lambda model: (client, provider))
        monkeypatch.setattr(agent_mod, "execute_tool", execute_tool)
        if search_budget is not None:
            monkeypatch.setattr(agent_mod, "SEARCH_CALLS_PER_MESSAGE", search_budget)
        kwargs = dict(
            system_prompt="You are a test agent.",
            model="minimax-m2",
            study_context_text=None,
            scope="global",
            chat_id="chat-test",
            tools=tools if tools is not None else [
                {"type": "function", "function": {"name": "search_studies", "parameters": {}}},
                {"type": "function", "function": {"name": "get_study_report", "parameters": {}}},
            ],
        )
        if max_iters is not None:
            kwargs["max_iters"] = max_iters
        events = list(agent_mod.stream_agent(
            messages or [{"role": "user", "content": "hi"}], **kwargs))
        return events, client, execute_tool

    return _run
