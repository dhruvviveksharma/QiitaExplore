"""Synchronous streaming client for the pi sidecar.

Uses httpx (the existing convention in this codebase — see qiita_client.py)
rather than requests, which isn't a declared dependency. Framing is strict
LF-only per pi's own spec: split on "\\n" only and strip a trailing "\\r";
U+2028/U+2029 are legal inside a JSON string and must never be treated as
line breaks.
"""

import json
import logging

import httpx

import config

logger = logging.getLogger(__name__)


class PiSidecarError(Exception):
    """The sidecar was unreachable, rejected the request, or errored mid-stream."""


def stream_chat(*, scope, chat_id, user_id, model, system_prompt, message,
                 context_block=None, tool_token, session_file=None,
                 timeout_seconds=290.0):
    """POST /chat/stream and yield one parsed pi event dict per NDJSON line.

    The sidecar only closes the response after the turn has fully settled
    (sessions.mjs's runTurn resolves on agent_settled, not on prompt()'s own
    preflight-acceptance resolution) — so by the time this generator is
    exhausted, the turn is genuinely done. Callers don't need to reason about
    pi's own willRetry/agent_settled distinction; that's already resolved on
    the sidecar side.

    timeout_seconds default (290s) is deliberately just UNDER gunicorn's
    --timeout 300 and nginx's proxy_read_timeout 300s (start_barnacle.sh,
    nginx.conf), not equal to them. This is the innermost hop, so it should
    time out FIRST and cleanly — raising PiSidecarError, caught by the route
    and turned into a proper SSE `error` event — rather than racing gunicorn's
    own worker timeout, which if it wins kills the whole WSGI worker with no
    graceful error frame at all. A prior default of 180s was actually
    shorter than a real deep search can take (measured at 61s for a plain
    search over a tunnelled Postgres — see docs/09-operations.md), so it was
    erroring out turns the outer 300s ladder was explicitly sized to allow.
    """
    # No deep_search field: the sidecar (server.mjs's handleChatStream) never
    # reads one — nothing under pi_sidecar/ references it. The real signal
    # already travels inside tool_token: mint_scope_token(..., deep_search=...)
    # signs it into the scope token, and internal_tool_routes.py reads it from
    # THERE when a tool call comes back in. A body field here would just be a
    # second, unused copy of the same information.
    # user_id scopes the sidecar's session directory and its session cache. The
    # sidecar trusts Flask for that ROUTING, exactly as it already trusts the
    # chat_id it is handed; the scope token remains the only thing it cannot
    # forge and stays the authority for tool calls. Do not try to read user_id
    # out of tool_token there — the sidecar deliberately holds no
    # PI_SCOPE_TOKEN_KEY, so it could only decode it unverified.
    #
    # session_file lets it reopen the transcript with SessionManager.open()
    # instead of scanning the session directory. None for chats predating the
    # pi_session_file column, which fall back to the scan.
    body = {
        "scope": scope,
        "chat_id": chat_id,
        "user_id": user_id,
        "session_file": session_file,
        "model": model,
        "system_prompt": system_prompt,
        "message": message,
        "context_block": context_block,
        "tool_token": tool_token,
    }
    url = f"{config.PI_SIDECAR_URL}/chat/stream"
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0)) as http_client:
            with http_client.stream(
                "POST", url, json=body,
                headers={"x-pi-secret": config.PI_SIDECAR_SECRET or ""},
            ) as resp:
                if resp.status_code != 200:
                    detail = resp.read().decode("utf-8", "replace")[:500]
                    raise PiSidecarError(f"sidecar returned {resp.status_code}: {detail}")
                buf = ""
                for chunk in resp.iter_text():
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.rstrip("\r")
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except ValueError:
                            logger.warning("pi sidecar emitted a non-JSON NDJSON line: %r", line[:200])
                trailing = buf.rstrip("\r\n")
                if trailing:
                    try:
                        yield json.loads(trailing)
                    except ValueError:
                        logger.warning("pi sidecar emitted a non-JSON trailing line: %r", trailing[:200])
    except httpx.TimeoutException as exc:
        raise PiSidecarError(f"sidecar timed out: {exc}") from exc
    except httpx.TransportError as exc:
        raise PiSidecarError(f"sidecar unreachable: {exc}") from exc


def delete_session(*, scope, chat_id, user_id, timeout_seconds=3.0):
    """Best-effort: dispose the sidecar's in-memory session and delete its
    JSONL file when a chat is deleted. Called from the chat-delete routes;
    failures are logged, not raised — a leaked pi session file is a cleanup
    debt, not a reason to fail the user's delete.

    Skips only when pi is not configured at all. Callers must NOT gate this on
    PI_BACKEND_GLOBAL/PI_BACKEND_PROJECT: those flags say "route new turns
    through pi", not "pi sessions exist". A chat created with the flag on and
    deleted after it was flipped off still has a JSONL to clean up, and gating
    on the flag is what left those orphaned (docs/09-operations.md)."""
    if not config.PI_SIDECAR_SECRET:
        return
    url = f"{config.PI_SIDECAR_URL}/session/delete"
    try:
        httpx.post(
            url, json={"scope": scope, "chat_id": chat_id, "user_id": user_id},
            headers={"x-pi-secret": config.PI_SIDECAR_SECRET or ""},
            timeout=timeout_seconds,
        )
    except httpx.HTTPError:
        logger.exception("failed to delete pi session for scope=%s chat_id=%s", scope, chat_id)


def abort_session(*, scope, chat_id, user_id, timeout_seconds=10.0):
    url = f"{config.PI_SIDECAR_URL}/abort"
    try:
        httpx.post(
            url, json={"scope": scope, "chat_id": chat_id, "user_id": user_id},
            headers={"x-pi-secret": config.PI_SIDECAR_SECRET or ""},
            timeout=timeout_seconds,
        )
    except httpx.HTTPError:
        logger.exception("failed to abort pi session for scope=%s chat_id=%s", scope, chat_id)
