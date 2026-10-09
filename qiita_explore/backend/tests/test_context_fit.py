"""A request too long for the model: recognizing the rejection, compacting
the request for one retry (helpers/context_fit.py), and what the user reads
(llm_helpers.friendly_llm_error).
"""
import anthropic
import httpx
import openai
import pytest

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()

import helpers.chat_history as ch  # noqa: E402
from helpers import context_fit as cf  # noqa: E402
from helpers.chat_transcript import rows_to_provider_messages  # noqa: E402
from helpers.context_usage import request_chars  # noqa: E402
from helpers.llm_helpers import friendly_llm_error, is_context_overflow, too_long_message  # noqa: E402

VLLM = ("This model's maximum context length is 131072 tokens. However, you requested 150000 tokens "
        "(146000 in the messages, 4000 in the completion). Please reduce the length of the messages or completion.")
VLLM_NEW = ("The decoder prompt (length 140000) is longer than the maximum model length of 131072. "
            "Make sure that `max_model_len` is no smaller than the number of text tokens.")
CLAUDE = "prompt is too long: 215000 tokens > 200000 maximum"


def _resp(status):
    return httpx.Response(status, request=httpx.Request("POST", "http://x"))


def openai_error(msg, status=400, cls=openai.BadRequestError):
    body = {"message": msg}
    return cls(f"Error code: {status} - {{'error': {body!r}}}", response=_resp(status), body=body)


def anthropic_error(msg, status=400, cls=anthropic.BadRequestError):
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": msg}}
    return cls(f"Error code: {status} - {body!r}", response=_resp(status), body=body)


class TestRecognize:

    @pytest.mark.parametrize("exc", [
        openai_error(VLLM), openai_error(VLLM_NEW), anthropic_error(CLAUDE),
        openai_error("context_length_exceeded"),
    ])
    def test_overflows(self, exc):
        assert is_context_overflow(exc)

    @pytest.mark.parametrize("exc", [
        openai_error("Invalid value for tool_choice"),
        anthropic_error("Number of request tokens has exceeded your per-minute rate limit",
                        status=429, cls=anthropic.RateLimitError),
        openai_error("maximum context length is 8 tokens", status=500, cls=openai.InternalServerError),
        Exception("rate limit: too many tokens per minute"),
    ])
    def test_not_overflows(self, exc):
        assert not is_context_overflow(exc)

    def test_counts_from_each_provider(self):
        assert cf.overflow_tokens(openai_error(VLLM)) == (150000, 131072)
        assert cf.overflow_tokens(openai_error(VLLM_NEW)) == (140000, 131072)
        assert cf.overflow_tokens(anthropic_error(CLAUDE)) == (215000, 200000)
        assert cf.overflow_tokens(openai_error("context_length_exceeded")) is None


class TestMessage:

    def test_overflow_says_too_long_on_both_providers(self):
        assert friendly_llm_error(openai_error(VLLM), "minimax-m2") == too_long_message("minimax-m2")
        assert friendly_llm_error(anthropic_error(CLAUDE), "claude-haiku-4-5") == too_long_message("claude-haiku-4-5")

    def test_anthropic_key_errors_still_mention_the_key(self):
        exc = anthropic_error("invalid x-api-key", status=401, cls=anthropic.AuthenticationError)
        assert "ANTHROPIC_API_KEY" in friendly_llm_error(exc, "claude-haiku-4-5")

    def test_anthropic_outage_is_unavailable(self):
        exc = anthropic_error("Overloaded", status=529, cls=anthropic.InternalServerError)
        assert "currently unavailable" in friendly_llm_error(exc, "claude-haiku-4-5")

    def test_other_anthropic_400_shows_its_own_text(self):
        msg = friendly_llm_error(anthropic_error("tools.0.input_schema: invalid"), "claude-haiku-4-5")
        assert msg == "tools.0.input_schema: invalid"


# ── recover ───────────────────────────────────────────────────────────────────

def _rows(n, size=6000):
    rows = []
    for i in range(n):
        rows.append({"id": 2 * i + 1, "role": "user", "content": f"question {i} " + "q" * size,
                     "model_transcript": None})
        rows.append({"id": 2 * i + 2, "role": "assistant", "content": f"answer {i} " + "a" * size,
                     "model_transcript": None})
    return rows


def _request(provider, rows, *, study_ctx="S" * 50_000, turn=()):
    """msgs + Layout exactly as stream_agent lays them out (route path)."""
    layout = cf.Layout("system prompt", study_ctx, "old summary", list(rows) if rows is not None else None, provider)
    history = rows_to_provider_messages(rows or [], provider)
    msgs = ([cf.system_message(layout)] + history
            + [{"role": "user", "content": "now this\n\n[slash hint]"}] + list(turn))
    layout.history_end = 1 + len(history)
    return msgs, layout


@pytest.fixture
def summarizer(monkeypatch):
    """The compaction summarizer and its persist, recorded instead of run."""
    seen = {"inputs": [], "persisted": []}

    def fake_llm_chat(messages, study_context_text, system_prompt, model=None):
        seen["inputs"].append(messages[0]["content"])
        return "NEW SUMMARY"

    monkeypatch.setattr(ch, "llm_chat", fake_llm_chat)
    monkeypatch.setattr(ch, "persist_compaction_state",
                        lambda chat_id, scope, **kw: seen["persisted"].append((chat_id, scope, kw)))
    monkeypatch.setattr(cf, "log_turn_event", lambda *a, **kw: seen.setdefault("log", []).append((a, kw)))
    return seen


def _recover(msgs, layout, exc, model="minimax-m2"):
    events = list(cf.recover(msgs, layout, exc=exc, model=model, chat_id="c1", scope="global"))
    return events


def _target(exc, chars_before):
    sent, limit = cf.overflow_tokens(exc)
    return int((limit - max(limit // 10, 8_000)) * chars_before / sent)


class TestRecover:

    def test_autocompacts_the_conversation_and_persists_the_summary(self, summarizer):
        msgs, layout = _request("nrp", _rows(6))
        events = _recover(msgs, layout, openai_error("context_length_exceeded"))   # no counts: 200k-token fallback

        assert [e["type"] for e in events] == ["step_start", "step_done"]
        assert "compacting" in events[0]["label"]
        assert events[1]["detail"] == "5 earlier turns summarized"
        # old summary folded in, newest turn kept verbatim, anchor after turn 5
        assert summarizer["inputs"][0].startswith("[Summary of even earlier conversation]:\nold summary")
        assert summarizer["persisted"] == [("c1", "global", {"summary": "NEW SUMMARY", "through_id": 10})]
        assert "NEW SUMMARY" in msgs[0]["content"]
        assert [m["content"][:10] for m in msgs[1:3]] == ["question 5", "answer 5 a"]
        assert msgs[layout.history_end]["content"] == "now this\n\n[slash hint]"
        assert layout.study_ctx == "S" * 50_000                  # stages 2–3 not needed

    def test_still_too_big_cuts_tool_results_then_study_context(self, summarizer):
        turn = [{"role": "assistant", "content": None, "tool_calls": [
                    {"id": "t1", "type": "function", "function": {"name": "search_studies", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "t1", "content": "R" * 30_000}]
        msgs, layout = _request("nrp", _rows(6), turn=turn)
        exc = anthropic_error(f"prompt is too long: {request_chars(msgs, None) // 2} tokens > 20000 maximum")
        target = _target(exc, request_chars(msgs, None))
        events = _recover(msgs, layout, exc)

        assert events[1]["detail"] == "5 earlier turns summarized · 1 tool result trimmed · study context trimmed"
        assert msgs[-1]["content"].endswith("…(trimmed to fit the context window)")
        assert len(msgs[-1]["content"]) < 2_100
        assert msgs[-2]["tool_calls"][0]["id"] == "t1"                       # this turn's exchange kept
        assert layout.study_ctx.endswith("[study context trimmed to fit the context window]")
        assert request_chars(msgs, None) <= target + 100

    def test_anthropic_shape(self, summarizer):
        turn = [{"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "search_studies", "input": {}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "R" * 30_000}]}]
        msgs, layout = _request("anthropic", _rows(3), turn=turn, study_ctx="S" * 100)
        exc = anthropic_error(f"prompt is too long: {request_chars(msgs, None) // 2} tokens > 8000 maximum")
        _recover(msgs, layout, exc, model="claude-haiku-4-5")

        assert msgs[layout.history_end]["content"] == "now this\n\n[slash hint]"
        assert msgs[-1]["content"][0]["content"].endswith("…(trimmed to fit the context window)")
        assert msgs[-2]["content"][0]["type"] == "tool_use"

    def test_one_giant_turn_is_summarized_too(self, summarizer):
        msgs, layout = _request("nrp", _rows(1, size=200_000))
        _recover(msgs, layout, openai_error("context_length_exceeded"))
        assert layout.history_end == 1                       # nothing kept verbatim
        assert summarizer["persisted"][0][2]["through_id"] == 2

    def test_a_failed_summary_drops_the_conversation(self, summarizer, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("summarizer down")
        monkeypatch.setattr(ch, "llm_chat", boom)
        msgs, layout = _request("nrp", _rows(4))
        events = _recover(msgs, layout, openai_error("context_length_exceeded"))

        assert layout.history_end == 1
        assert "[Earlier conversation omitted to fit the context window.]" in msgs[0]["content"]
        assert events[1]["detail"] == "earlier messages left out"
        assert any(a[1] == "compaction_failed" for a, _ in summarizer["log"])
        assert summarizer["persisted"] == []

    def test_harness_path_drops_history(self, summarizer):
        msgs, layout = _request("nrp", None)
        msgs[1:1] = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
        layout.history_end = 3
        _recover(msgs, layout, openai_error("context_length_exceeded"))
        assert [m["role"] for m in msgs] == ["system", "user"]
        assert summarizer["inputs"] == []
