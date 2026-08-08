"""Default-deny auth guard: resolves the session cookie to g.user_id and
rejects unauthenticated or CSRF-missing requests to every non-public endpoint.

Two before_request hooks, registered together via register_auth_middleware():
  1. _load_session — cookie -> session row -> g.user_id / g.session_row.
  2. _require_auth — default-deny + CSRF check on state-changing requests.
"""

import hmac

from flask import g, jsonify, request

import config
from store.auth_store import get_session_by_token, touch_session

# Exact Flask endpoint names (view function names) that never require a
# session. Everything else defaults to deny. Do NOT use path prefixes here —
# an accidental prefix match is exactly the kind of hole this guard exists
# to close.
PUBLIC_ENDPOINTS = {
    "api_auth_login_url",
    "api_auth_connect",
    "api_auth_me",
}

_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def register_auth_middleware(app):
    @app.before_request
    def _load_session():
        g.user_id = None
        g.session_row = None
        if request.method == "OPTIONS":
            return None

        raw_token = request.cookies.get(config.SESSION_COOKIE_NAME)
        if not raw_token:
            return None

        row = get_session_by_token(raw_token)
        if row is None:
            return None

        touch_session(row["session_hash"], row["last_seen_at"])
        g.user_id = row["user_id"]
        g.session_row = row
        return None

    @app.before_request
    def _require_auth():
        if request.method == "OPTIONS":
            return None
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if request.endpoint is None:
            # Unknown route — let Flask's normal 404 handling take over.
            return None
        if g.get("user_id") is None:
            return jsonify({"error": "authentication required"}), 401
        if request.method in _STATE_CHANGING_METHODS:
            csrf_header = request.headers.get("X-CSRF-Token", "")
            expected = (g.session_row or {}).get("csrf_token", "")
            if not expected or not hmac.compare_digest(csrf_header, expected):
                return jsonify({"error": "CSRF token missing or invalid"}), 403
        return None
