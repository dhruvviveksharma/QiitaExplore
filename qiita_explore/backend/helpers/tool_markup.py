"""Tool calls a model writes out as plain text. minimax-m2, asked for a final
answer with no tools offered, still writes its calls in its own format —
`]<]minimax[>[<tool_call>…<invoke name="…">…` — and a server that fails to
parse a call passes it through the same way. MarkupGate keeps that text off the
screen, out of the stored message and out of the history replayed next turn
(helpers/agent.py)."""

_MARKERS = ("]<]minimax[>[", "<minimax:tool_call>", "<tool_call>")


class MarkupGate:
    """feed() each streamed chunk and show what it returns; flush() at the end.
    From the first marker to the end of the response everything is dropped
    (`tripped`). A tail that could still turn into a marker is held back until
    the next chunk settles it."""

    def __init__(self):
        self.tripped = False
        self._held = ""

    def feed(self, text: str) -> str:
        if self.tripped:
            return ""
        buf = self._held + text
        hits = [i for i in (buf.find(m) for m in _MARKERS) if i >= 0]
        if hits:
            self.tripped, self._held = True, ""
            return buf[:min(hits)]
        keep = _partial_marker_len(buf)
        self._held = buf[len(buf) - keep:]
        return buf[:len(buf) - keep]

    def flush(self) -> str:
        out, self._held = ("" if self.tripped else self._held), ""
        return out


def _partial_marker_len(buf: str) -> int:
    """Length of the longest end of `buf` that is the start of a marker."""
    best = 0
    for m in _MARKERS:
        for n in range(min(len(m) - 1, len(buf)), best, -1):
            if buf.endswith(m[:n]):
                best = n
                break
    return best
