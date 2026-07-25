"""HMAC-signed scope tokens carried by the pi sidecar on every internal tool
call, so the internal tool route — not the sidecar — is the authority on what
a given chat turn is allowed to touch. Minted once per turn by the chat route,
short-lived (default 10 min): long enough to cover one slow tool-heavy turn,
short enough that a leaked token is useless well before the next one is minted.

Not a JWT (no external dependency, no algorithm-confusion surface): a
fixed-shape payload, base64url-encoded, HMAC-SHA256 signed with
config.PI_SCOPE_TOKEN_KEY. Verification is constant-time.
"""

import base64
import hashlib
import hmac
import json
import time

import config


class ScopeTokenError(Exception):
    pass


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _key() -> bytes:
    key = config.PI_SCOPE_TOKEN_KEY
    if not key:
        raise ScopeTokenError("PI_SCOPE_TOKEN_KEY is not configured")
    return key.encode("utf-8")


def mint_scope_token(*, user_id: str, scope: str, chat_id: str,
                      project_id: str = None, deep_search: bool = False,
                      ttl_seconds: int = 600) -> str:
    """Sign a token carrying exactly what an internal tool call is allowed to
    touch this turn. scope='project' without project_id is a programming
    error in the caller, not something this function should paper over."""
    if scope == "project" and not project_id:
        raise ScopeTokenError("project scope requires project_id")
    payload = {
        "user_id": user_id,
        "scope": scope,
        "chat_id": chat_id,
        "project_id": project_id,
        "deep_search": bool(deep_search),
        "exp": time.time() + ttl_seconds,
    }
    body = _b64u_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64u_encode(hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_scope_token(token: str) -> dict:
    if not token or "." not in token:
        raise ScopeTokenError("malformed token")
    body, _, sig = token.partition(".")
    expected_sig = _b64u_encode(hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected_sig):
        raise ScopeTokenError("signature mismatch")
    try:
        payload = json.loads(_b64u_decode(body))
    except (ValueError, UnicodeDecodeError):
        raise ScopeTokenError("malformed payload")
    if payload.get("exp", 0) < time.time():
        raise ScopeTokenError("token expired")
    if payload.get("scope") not in ("global", "project"):
        raise ScopeTokenError("invalid scope")
    if payload.get("scope") == "project" and not payload.get("project_id"):
        raise ScopeTokenError("project scope missing project_id")
    return payload
