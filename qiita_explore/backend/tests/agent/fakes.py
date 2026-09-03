"""Fake LLM provider clients for loop-level agent tests.

Plain-Python stand-ins for the OpenAI / Anthropic SDK clients, reproducing
exactly the chunk/event attributes helpers/agent.py consumes — no HTTP-level
mocking, no network. Scripts are per-round: script[0] answers the first LLM
call of a turn, script[1] the second, and the last round is reused for any
further calls (which covers the forced-synthesis extra call for free).

A script round may also be an Exception instance: that call raises instead of
streaming (for retry tests).
"""
from helpers.agent_tools import ToolResult


# ── OpenAI/NRP shapes ─────────────────────────────────────────────────────────

class FakeFunction:
    def __init__(self, name=None, arguments=None):
        self.name, self.arguments = name, arguments


class FakeToolCallDelta:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index, self.id = index, id
        self.function = FakeFunction(name, arguments)


class FakeDelta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls or []


class FakeChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta, self.finish_reason = delta, finish_reason


class FakeChunk:
    def __init__(self, choices):
        self.choices = choices  # [] emulates a heartbeat chunk


class FakeOpenAIClient:
    def __init__(self, script):
        self._script, self._i = script, 0
        self.calls = []  # captured kwargs per .create() call
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.calls.append(kw)
                round_ = outer._script[min(outer._i, len(outer._script) - 1)]
                outer._i += 1
                if isinstance(round_, Exception):
                    raise round_
                return iter(round_)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def openai_text_round(text, finish_reason="stop"):
    """One LLM round streaming `text` char-by-char, then the finish chunk."""
    return [FakeChunk([FakeChoice(FakeDelta(content=t))]) for t in text] + \
           [FakeChunk([FakeChoice(FakeDelta(), finish_reason=finish_reason)])]


def openai_tool_call_round(call_id, name, args_json):
    """One LLM round emitting a single fragmented tool call."""
    return [
        FakeChunk([FakeChoice(FakeDelta(tool_calls=[FakeToolCallDelta(0, id=call_id, name=name)]))]),
        FakeChunk([FakeChoice(FakeDelta(tool_calls=[FakeToolCallDelta(0, arguments=args_json)]))]),
        FakeChunk([FakeChoice(FakeDelta(), finish_reason="tool_calls")]),
    ]


def openai_two_tool_calls_round(id_a, id_b, name, args_json):
    """One round with two parallel tool calls to the same tool (index 0 and 1)."""
    return [
        FakeChunk([FakeChoice(FakeDelta(tool_calls=[FakeToolCallDelta(0, id=id_a, name=name)]))]),
        FakeChunk([FakeChoice(FakeDelta(tool_calls=[FakeToolCallDelta(0, arguments=args_json)]))]),
        FakeChunk([FakeChoice(FakeDelta(tool_calls=[FakeToolCallDelta(1, id=id_b, name=name)]))]),
        FakeChunk([FakeChoice(FakeDelta(tool_calls=[FakeToolCallDelta(1, arguments=args_json)]))]),
        FakeChunk([FakeChoice(FakeDelta(), finish_reason="tool_calls")]),
    ]


# ── Anthropic shapes ──────────────────────────────────────────────────────────

class FakeContentBlock:
    def __init__(self, type, id=None, name=None):
        self.type, self.id, self.name = type, id, name


class FakeAnthDelta:
    def __init__(self, type=None, text=None, partial_json=None, stop_reason=None):
        self.type, self.text = type, text
        self.partial_json, self.stop_reason = partial_json, stop_reason


class FakeAnthEvent:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeAnthStreamCtx:
    def __init__(self, events):
        self._events = events

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._events)


class FakeAnthropicClient:
    def __init__(self, script):
        self._script, self._i = script, 0
        self.calls = []
        outer = self

        class _Messages:
            def stream(self, **kw):
                outer.calls.append(kw)
                round_ = outer._script[min(outer._i, len(outer._script) - 1)]
                outer._i += 1
                if isinstance(round_, Exception):
                    raise round_
                return _FakeAnthStreamCtx(round_)

        self.messages = _Messages()


def anthropic_text_round(text):
    return [
        FakeAnthEvent("content_block_start", content_block=FakeContentBlock("text")),
        FakeAnthEvent("content_block_delta", delta=FakeAnthDelta(type="text_delta", text=text)),
        FakeAnthEvent("content_block_stop"),
        FakeAnthEvent("message_delta", delta=FakeAnthDelta(stop_reason="end_turn")),
    ]


def anthropic_tool_use_round(call_id, name, json_fragments):
    """One round emitting a tool_use block whose input arrives as fragmented
    input_json_delta events, then stop_reason='tool_use'."""
    events = [FakeAnthEvent("content_block_start",
                            content_block=FakeContentBlock("tool_use", id=call_id, name=name))]
    events += [FakeAnthEvent("content_block_delta",
                             delta=FakeAnthDelta(type="input_json_delta", partial_json=f))
               for f in json_fragments]
    events += [
        FakeAnthEvent("content_block_stop"),
        FakeAnthEvent("message_delta", delta=FakeAnthDelta(stop_reason="tool_use")),
    ]
    return events


# ── Tool-execution boundary ───────────────────────────────────────────────────

def tool_result(text="tool result", label="Done", detail="d", ui_payload=None, executed=True):
    return ToolResult(text=text, label=label, detail=detail,
                      ui_payload=ui_payload, executed=executed)


def make_fake_execute_tool(*results):
    """Scripted execute_tool: consumes one entry per call, in call order.
    An Exception entry raises on that call. `.calls` records (name, args)."""
    it = iter(results)

    def _fake(name, args, *, scope, chat_id, deep_search=False):
        _fake.calls.append((name, args))
        nxt = next(it)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    _fake.calls = []
    return _fake


def drain(gen):
    """Drive a generator to completion; return the list of yielded events."""
    return list(gen)


def tokens_of(events):
    return "".join(e["token"] for e in events if e["type"] == "token")


def events_of_type(events, etype):
    return [e for e in events if e["type"] == etype]
