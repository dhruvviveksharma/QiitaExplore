"""MarkupGate on its own: markers split across chunks, held tails, and
nothing after a trip (helpers/tool_markup.py)."""
from helpers.tool_markup import MarkupGate


def _run(*chunks):
    gate = MarkupGate()
    shown = "".join(gate.feed(c) for c in chunks) + gate.flush()
    return shown, gate.tripped


def test_plain_text_passes_through():
    assert _run("hello ", "world") == ("hello world", False)


def test_marker_split_across_chunks_is_dropped_with_everything_after():
    assert _run("answer.]<]mi", "nimax[>", "[<tool_call>", " more") == ("answer.", True)


def test_each_marker_trips():
    for marker in ("]<]minimax[>[", "<minimax:tool_call>", "<tool_call>"):
        assert _run("x", marker, "y") == ("x", True)


def test_a_held_tail_is_released_when_it_never_becomes_a_marker():
    gate = MarkupGate()
    assert gate.feed("ends with <tool") == "ends with "
    assert gate.flush() == "<tool"
    assert not gate.tripped
