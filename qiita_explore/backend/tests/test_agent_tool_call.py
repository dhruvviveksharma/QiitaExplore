"""Unit tests for helpers.agent._execute_tool_call().

Drives the generator directly and asserts on the two SSE event dicts it yields
and the (result_text, is_search_studies) return value it produces via StopIteration.
No DB, no network, no LLM required.
"""
import sys
import types
import pytest
from unittest.mock import MagicMock, patch


def _collect(gen):
    """Drive a generator to completion; return (events, return_value)."""
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        return events, stop.value


def _make_result(text="result text", label="Done", detail="some detail", ui_payload=None):
    result = MagicMock()
    result.text = text
    result.label = label
    result.detail = detail
    result.ui_payload = ui_payload
    return result


@pytest.fixture(scope="module", autouse=True)
def stub_agent_imports():
    """Stub heavy top-level imports so helpers.agent can be imported in a unit test."""
    # Build a minimal config stub
    config_stub = types.ModuleType("config")
    config_stub.client = MagicMock()
    config_stub.get_client = MagicMock(return_value=(MagicMock(), "nrp"))
    config_stub.anthropic_client = MagicMock()
    config_stub.DEFAULT_MODEL = "gemma"
    config_stub.ALLOWED_MODELS = {"gemma"}
    config_stub.MODEL_METADATA = {"gemma": {"context": 8192, "supports_tools": True}}

    # Build minimal llm_helpers stub
    llm_stub = types.ModuleType("helpers.llm_helpers")
    llm_stub._build_api_messages = MagicMock(return_value=[])
    llm_stub._extract_system_and_messages = MagicMock(return_value=("", []))
    llm_stub._resolve_model = MagicMock(side_effect=lambda m: m or "gemma")
    llm_stub.friendly_llm_error = MagicMock(return_value="error")

    # Build minimal agent_tools stub (execute_tool will be patched per-test)
    tools_stub = types.ModuleType("helpers.agent_tools")
    tools_stub.TOOL_SCHEMAS = []
    tools_stub.execute_tool = MagicMock()

    old = {}
    for name, stub in [("config", config_stub),
                       ("helpers.llm_helpers", llm_stub),
                       ("helpers.agent_tools", tools_stub)]:
        old[name] = sys.modules.get(name)
        sys.modules[name] = stub

    # Force fresh import of helpers.agent with stubs in place
    sys.modules.pop("helpers.agent", None)

    import helpers.agent  # noqa: F401 — registers in sys.modules

    yield

    # Restore
    for name, orig in old.items():
        if orig is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = orig
    sys.modules.pop("helpers.agent", None)


# ---------------------------------------------------------------------------

class TestExecuteToolCall:

    def test_happy_path_events_and_return(self):
        """Yields segment_tool_call then segment_tool_result; returns (text, False)."""
        import helpers.agent as agent_mod
        mock_result = _make_result(text="T", label="L", detail="D", ui_payload={"k": 1})

        with patch.object(agent_mod, "execute_tool", return_value=mock_result):
            events, retval = _collect(
                agent_mod._execute_tool_call(
                    "get_study_report", {"study_id": 11043}, "abc123def456",
                    scope="global", chat_id="chat1", deep_search=False,
                    search_already_done=False,
                )
            )

        assert len(events) == 2

        call_ev = events[0]
        assert call_ev["type"] == "segment_tool_call"
        assert call_ev["name"] == "tool_get_study_report_abc123"
        assert call_ev["args"] == {"study_id": 11043}
        assert "label" in call_ev

        result_ev = events[1]
        assert result_ev["type"] == "segment_tool_result"
        assert result_ev["name"] == "tool_get_study_report_abc123"
        assert result_ev["label"] == "L"
        assert "D" in result_ev["detail"]
        assert "s" in result_ev["detail"]   # timing suffix e.g. "D · 0.0s"
        assert result_ev["ui_payload"] == {"k": 1}

        assert retval == ("T", False)

    def test_search_studies_returns_true_flag(self):
        """is_search_studies is True only when name=='search_studies'."""
        import helpers.agent as agent_mod
        mock_result = _make_result(text="results", label="Done", detail="")

        with patch.object(agent_mod, "execute_tool", return_value=mock_result):
            _, retval = _collect(
                agent_mod._execute_tool_call(
                    "search_studies", {"keywords": ["mouse"]}, "xyz999abc111",
                    scope="global", chat_id="chat1", deep_search=False,
                    search_already_done=False,
                )
            )

        assert retval == ("results", True)

    def test_non_search_tool_returns_false_flag(self):
        """is_search_studies is False for any tool other than search_studies."""
        import helpers.agent as agent_mod
        mock_result = _make_result(text="pinned", label="Pinned", detail="")

        with patch.object(agent_mod, "execute_tool", return_value=mock_result):
            _, retval = _collect(
                agent_mod._execute_tool_call(
                    "pin_study", {"study_ids": [11043]}, "aaa000bbb111",
                    scope="global", chat_id="chat1", deep_search=False,
                    search_already_done=False,
                )
            )

        assert retval[1] is False

    def test_tool_failure_yields_error_event_and_returns_failure_text(self):
        """When execute_tool raises, yields failure segment_tool_result; returns (error_text, False)."""
        import helpers.agent as agent_mod

        with patch.object(agent_mod, "execute_tool", side_effect=RuntimeError("boom")):
            events, retval = _collect(
                agent_mod._execute_tool_call(
                    "get_study_report", {"study_id": 99}, "fail12345678",
                    scope="global", chat_id="chat1", deep_search=False,
                    search_already_done=False,
                )
            )

        assert len(events) == 2
        assert events[0]["type"] == "segment_tool_call"

        result_ev = events[1]
        assert result_ev["type"] == "segment_tool_result"
        assert "failed" in result_ev["label"]
        assert result_ev["ui_payload"] is None
        assert "boom" in result_ev["detail"]

        text, is_search = retval
        assert "boom" in text or "failed" in text.lower()
        assert is_search is False

    def test_empty_detail_omits_dot_prefix(self):
        """When ToolResult.detail is empty, timing string has no leading '·'."""
        import helpers.agent as agent_mod
        mock_result = _make_result(text="T", label="L", detail="")

        with patch.object(agent_mod, "execute_tool", return_value=mock_result):
            events, _ = _collect(
                agent_mod._execute_tool_call(
                    "get_study_report", {}, "detl00000000",
                    scope="global", chat_id="chat1", deep_search=False,
                    search_already_done=False,
                )
            )

        detail = events[1]["detail"]
        assert not detail.startswith("·"), f"Expected no leading '·', got: {detail!r}"
        assert "s" in detail   # timing like "0.0s"
