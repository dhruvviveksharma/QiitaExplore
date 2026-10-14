"""Default-deny auth guard: resolves the session cookie to g.user_id and
rejects unauthenticated or CSRF-missing requests to every non-public endpoint.

Two before_request hooks, registered together via register_auth_middleware():
  1. _load_session — cookie -> session row -> g.user_id / g.session_row. A
     classic-Qiita session also re-reads its account every RECHECK_SECONDS, so a
     password changed (or an account deleted or unverified) on Qiita ends it. A
     qiita-miint session was proved once by whoami and keeps no token to re-check
     with; it lasts to its absolute expiry.
  2. _require_auth — default-deny + CSRF check on state-changing requests.
"""

import hmac
import logging
from datetime import datetime

from flask import g, jsonify, request

import config
from helpers import qiita_accounts as accounts
from store.auth_store import (get_session_by_token, mark_session_verified, revoke_session, revoke_user_sessions,
                              touch_session)

logger = logging.getLogger(__name__)

# Exact Flask endpoint names (view function names) that never require a
# session. Everything else defaults to deny. Do NOT use path prefixes here —
# an accidental prefix match is exactly the kind of hole this guard exists
# to close.
PUBLIC_ENDPOINTS = {
    "api_auth_login",
    "api_auth_connect",
    "api_auth_login_url",
    "api_auth_login_key",
    "api_auth_me",
}

_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

RECHECK_SECONDS = 5 * 60
# Qiita's user table unreachable: keep serving a session this long after its
# last good check, then answer 503 (not revoke) until Qiita is back.
RECHECK_GRACE_SECONDS = 3600

# The login method behind each session source; "paste" is production's
# qiita-miint sessions from before the source was named.
_SOURCE_METHOD = {"qiita-classic": "classic", "qiita-miint": "miint", "paste": "miint"}


def _age_seconds(iso_value) -> float:
    try:
        return (datetime.utcnow() - datetime.fromisoformat(iso_value.rstrip("Z"))).total_seconds()
    except (AttributeError, ValueError):
        return float("inf")


def _still_valid(row) -> bool:
    """Re-read the account behind a session. False ends the session; raises
    AccountsUnavailable when Qiita can't be asked."""
    email, fp = row.get("email"), row.get("password_fp")
    if not email or not fp:
        return False
    acct = accounts.fetch_account(email)
    return (acct is not None and not acct.unverified
            and accounts.same_fingerprint(fp, accounts.password_fingerprint(acct.password_hash)))


def register_auth_middleware(app):
    @app.before_request
    def _load_session():
        g.user_id = None
        g.session_row = None
        g.auth_unavailable = False
        if request.method == "OPTIONS":
            return None

        raw_token = request.cookies.get(config.SESSION_COOKIE_NAME)
        if not raw_token:
            return None

        row = get_session_by_token(raw_token)
        if row is None:
            return None
        if _SOURCE_METHOD.get(row.get("source")) not in config.LOGIN_METHODS:
            # Its login method is switched off (likely after trouble with it):
            # ended for good, not just hidden until it's switched back on.
            revoke_session(row["session_hash"])
            return None

        age = _age_seconds(row.get("last_verified_at"))
        if row.get("source") == "qiita-classic" and age > RECHECK_SECONDS:
            try:
                valid = _still_valid(row)
            except accounts.AccountsUnavailable:
                if age > RECHECK_GRACE_SECONDS:
                    g.auth_unavailable = True
                    g.session_row = row          # only so logout can still revoke it
                    return None
            else:
                if not valid:
                    # The account changed on Qiita: end all of its sessions,
                    # qiita-miint ones included, not just this one.
                    revoke_user_sessions(row["user_id"])
                    return None
                mark_session_verified(row["session_hash"])

        touch_session(row["session_hash"], row["last_seen_at"])
        g.user_id = row["user_id"]
        g.session_row = row
        return None

    @app.before_request
    def _require_auth():
        if request.method == "OPTIONS":
            return None
        # Logging out is always allowed: it only revokes a local session.
        logout_while_unavailable = g.get("auth_unavailable") and request.endpoint == "api_auth_logout"
        if g.get("auth_unavailable") and not logout_while_unavailable:
            resp = jsonify({"error": "Qiita's user database is unreachable — try again shortly."})
            resp.headers["Retry-After"] = "30"
            return resp, 503
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if request.endpoint is None:
            # Unknown route — let Flask's normal 404 handling take over.
            return None
        if g.get("user_id") is None and not logout_while_unavailable:
            return jsonify({"error": "authentication required"}), 401
        if request.method in _STATE_CHANGING_METHODS:
            csrf_header = request.headers.get("X-CSRF-Token", "")
            expected = (g.session_row or {}).get("csrf_token", "")
            if not expected or not hmac.compare_digest(csrf_header, expected):
                return jsonify({"error": "CSRF token missing or invalid"}), 403
        return None
