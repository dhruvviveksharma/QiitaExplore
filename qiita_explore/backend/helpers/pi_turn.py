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

import logging

from helpers.llm_helpers import _sse
from helpers.pi_client import stream_chat as pi_stream_chat, abort_session as pi_abort_session
from helpers.pi_translate import TurnTranslator

logger = logging.getLogger(__name__)


def stream_pi_turn(**kwargs):
    """Yield SSE strings for one pi turn; return (assistant_parts, ui_payload).

    kwargs are passed straight through to helpers.pi_client.stream_chat.
    """
    translator = TurnTranslator()
    assistant_parts = []

    try:
        for sse_name, payload in translator.run(pi_stream_chat(**kwargs)):
            if sse_name == "token":
                assistant_parts.append(payload["token"])
            yield _sse(sse_name, payload)
    except GeneratorExit:
        # The caller (the Flask SSE route) stopped iterating us before the
        # turn settled — almost always because the browser disconnected (a
        # new message sent before this one finished, or the tab closed).
        # Without telling the sidecar, its in-flight turn keeps running into
        # a dead response — burning NRP tokens — and its per-session turn
        # queue (pi_sidecar/sessions.mjs) stays occupied until it finishes on
        # its own, so the next request for this chat just waits behind it.
        # Best-effort and fire-and-forget: cleanup code running because of a
        # GeneratorExit cannot itself yield a value back out.
        logger.info("pi turn abandoned mid-stream, aborting sidecar session: scope=%s chat_id=%s",
                    kwargs.get("scope"), kwargs.get("chat_id"))
        pi_abort_session(scope=kwargs.get("scope"), chat_id=kwargs.get("chat_id"))
        raise

    ui_payload = (
        {"kind": "agent_segments", "segments": translator.segments}
        if translator.segments else None
    )
    return assistant_parts, ui_payload
