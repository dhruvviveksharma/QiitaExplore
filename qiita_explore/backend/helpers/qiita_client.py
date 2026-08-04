"""Synchronous client for the Qiita control-plane's auth/whoami endpoint.

Mirrors the bearer-token pattern in qiita-common's ControlPlaneClient, but
synchronous (httpx.Client, not httpx.AsyncClient) since this Flask backend is
not async. Never logs the PAT itself — only status codes / exception types.
"""

import logging

import httpx

import config

logger = logging.getLogger(__name__)


class WhoAmIResult:
    """Outcome of a whoami call.

    `transient_error=True` means the call failed for a reason unrelated to the
    PAT's validity (timeout, connection error, 5xx) — callers must NOT treat
    this the same as an invalid/expired/revoked token (401): a transient
    failure should not destroy a local session, only defer trusting it.
    """

    def __init__(self, *, ok: bool, identity: dict = None, transient_error: bool = False):
        self.ok = ok
        self.identity = identity
        self.transient_error = transient_error


def whoami(pat: str) -> WhoAmIResult:
    """Call GET /api/v1/auth/whoami with `pat` as a bearer token.

    Returns:
      - ok=True, identity=<dict>            for a valid human principal
      - ok=False, transient_error=False     ONLY for 401, or a 200 whose kind is
                                             anonymous/service — the two answers
                                             that definitively say this PAT is
                                             not a valid identity. Callers may
                                             revoke on these.
      - ok=False, transient_error=True      for everything else: timeouts,
                                             connect errors, 5xx, and any other
                                             unexpected status. Callers give a
                                             bounded availability grace and must
                                             NOT log the user out.
    """
    url = f"{config.QIITA_CONTROL_PLANE_URL}/api/v1/auth/whoami"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {pat}"},
            timeout=config.QIITA_WHOAMI_TIMEOUT_SECONDS,
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logger.warning("whoami transient failure: %s", type(exc).__name__)
        return WhoAmIResult(ok=False, transient_error=True)

    if resp.status_code == 401:
        return WhoAmIResult(ok=False, transient_error=False)
    if resp.status_code >= 500:
        logger.warning("whoami upstream 5xx: %s", resp.status_code)
        return WhoAmIResult(ok=False, transient_error=True)
    if resp.status_code != 200:
        # Transient, NOT definitive. Only a 401 means "this PAT is bad"; a 403,
        # a redirect, or a proxy error says nothing about the credential, and
        # treating those as definitive silently destroyed live sessions.
        logger.warning("whoami unexpected status: %s (treating as transient)", resp.status_code)
        return WhoAmIResult(ok=False, transient_error=True)

    try:
        body = resp.json()
    except ValueError:
        return WhoAmIResult(ok=False, transient_error=True)

    if body.get("kind") != "human":
        # Reject anonymous and service-account principals outright — only a
        # human PAT is a valid QiitaExplore identity.
        return WhoAmIResult(ok=False, transient_error=False)

    return WhoAmIResult(ok=True, identity=body)
