"""One pi-backed chat turn, as SSE.

Both stream routes assembled this identically — mint a scope token, stream the
sidecar, translate to SSE, accumulate segments — so it lives here once. Follows
the sub-generator convention already used by helpers/pin_flow.py::stream_pin_flow
and helpers/request_utils.py::stream_samples_report: the caller writes

    assistant_parts, ui_payload = yield from stream_pi_turn(...)

and the routes keep only the part that genuinely differs between them, which is
how the context block is built.

Segments come from the same walk that produces the SSE events (see
helpers/pi_translate.TurnTranslator). The earlier shape buffered every event so
a second pass could rebuild them, which both held the whole turn in memory and
measured tool durations against the replay rather than the call — every
persisted tool card rendered "· 0.0s".
"""

from helpers.llm_helpers import _sse
from helpers.pi_client import stream_chat as pi_stream_chat
from helpers.pi_translate import TurnTranslator


def stream_pi_turn(**kwargs):
    """Yield SSE strings for one pi turn; return (assistant_parts, ui_payload).

    kwargs are passed straight through to helpers.pi_client.stream_chat.
    """
    translator = TurnTranslator()
    assistant_parts = []

    for sse_name, payload in translator.run(pi_stream_chat(**kwargs)):
        if sse_name == "token":
            assistant_parts.append(payload["token"])
        yield _sse(sse_name, payload)

    ui_payload = (
        {"kind": "agent_segments", "segments": translator.segments}
        if translator.segments else None
    )
    return assistant_parts, ui_payload
