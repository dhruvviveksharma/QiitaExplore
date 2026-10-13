"""Classic-Qiita login: POST /api/auth/login, GET /api/auth/me, POST /api/auth/logout.

Users log in with their classic-Qiita email + password, checked against
qiita.qiita_user (helpers/qiita_accounts.py). Every check fails closed, in this
order: body size, JSON only (stops HTML-form login CSRF), exact Origin, format,
brute-force limits (helpers/login_limiter.py), then the account and the bcrypt
compare. Unknown email and wrong password answer identically. The password is
never logged, stored, put in an error or echoed back. docs/02-authentication.md.
"""
import ipaddress
import json
import logging

from flask import g, jsonify, request

import config
from run import app
from helpers import login_limiter as limiter
from helpers import qiita_accounts as accounts
from store.auth_store import create_session, get_user, purge_expired_sessions, resolve_user, revoke_session

logger = logging.getLogger(__name__)

_MAX_LOGIN_BODY = 8 * 1024
_GENERIC_401 = "Incorrect email or password."


def _cookie_kwargs():
    # No Domain: with Secure on the names carry __Host- (host-only, path /).
    return dict(httponly=True, samesite="Lax", secure=config.SESSION_COOKIE_SECURE, path="/")


def _set_session_cookie(resp, raw_token: str):
    resp.set_cookie(config.SESSION_COOKIE_NAME, raw_token,
                    max_age=config.AUTH_SESSION_ABSOLUTE_TTL_SECONDS, **_cookie_kwargs())


def _clear_session_cookie(resp):
    resp.delete_cookie(config.SESSION_COOKIE_NAME, path="/", secure=config.SESSION_COOKIE_SECURE)


def _origin_error():
    """None when the request's Origin is QiitaExplore's own; else (body, status).
    Refuses outright when no origin is configured: there is nothing to check
    against, and guessing from Host is unreliable behind Cloudflare + Caddy."""
    allowed = set(config.ALLOWED_ORIGINS)
    if config.PUBLIC_ORIGIN:
        allowed.add(config.PUBLIC_ORIGIN)
    if not allowed:
        logger.error("login refused: set QIITA_EXPLORE_PUBLIC_ORIGIN (or QIITA_EXPLORE_ALLOWED_ORIGINS)")
        return {"error": "Login is not configured on this server."}, 503
    origin = request.headers.get("Origin")
    if origin not in allowed:
        return {"error": "origin not allowed"}, 403
    # same-site only for a dev origin (frontend and backend on different ports).
    site = request.headers.get("Sec-Fetch-Site")
    ok_sites = ("same-origin", "same-site") if origin in config.ALLOWED_ORIGINS else ("same-origin",)
    if site and site not in ok_sites:
        return {"error": "origin not allowed"}, 403
    return None


def _client_ip():
    """The real client IP, from the configured trusted header only. Its last
    entry is the one the trusted proxy wrote; earlier ones are the client's own."""
    if not config.CLIENT_IP_HEADER:
        return None
    raw = (request.headers.get(config.CLIENT_IP_HEADER) or "").split(",")[-1].strip()
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def _identity_payload(user_row: dict) -> dict:
    return {
        "user_id": user_row["user_id"],
        "email": user_row.get("qiita_email"),
        "level": user_row.get("user_level"),
    }


def _limited(retry_after: int):
    minutes = max(1, (retry_after + 59) // 60)
    resp = jsonify({"error": f"Too many attempts — try again in {minutes} minute{'s' if minutes != 1 else ''}.",
                    "retry_after": retry_after})
    resp.headers["Retry-After"] = str(retry_after)
    return resp, 429


def _busy():
    resp = jsonify({"error": "The server is busy — try again in a moment."})
    resp.headers["Retry-After"] = "2"
    return resp, 503


def _unreachable():
    return jsonify({"error": "Qiita's user database is unreachable — try again shortly."}), 503


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    if (request.content_length or 0) > _MAX_LOGIN_BODY:
        return jsonify({"error": "request too large"}), 413
    if not request.is_json:
        return jsonify({"error": "JSON body required"}), 415
    err = _origin_error()
    if err:
        return jsonify(err[0]), err[1]
    # Bounded read: a chunked body has no Content-Length for the check above.
    raw = request.stream.read(_MAX_LOGIN_BODY + 1)
    if len(raw) > _MAX_LOGIN_BODY:
        return jsonify({"error": "request too large"}), 413
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return jsonify({"error": "JSON body required"}), 415

    email = accounts.normalize_email(data.get("email"))
    password = data.get("password")
    if email is None or not accounts.valid_password_format(password):
        return jsonify({"error": _GENERIC_401}), 401        # not counted: no bcrypt work was done

    device_token = request.cookies.get(config.DEVICE_COOKIE_NAME)
    client_ip = _client_ip()
    try:
        allowed, retry_after, via_device = limiter.reserve(email, client_ip, device_token)
    except limiter.LimiterUnavailable:
        logger.exception("login limiter unavailable")
        return _unreachable()
    if not allowed:
        return _limited(retry_after)

    try:
        acct = accounts.fetch_account(email)
        ok = accounts.check_password(password, acct.password_hash) if acct else accounts.burn_check(password)
    except accounts.AccountsUnavailable:
        return _unreachable()
    except accounts.Busy:
        return _busy()
    if not ok:
        return jsonify({"error": _GENERIC_401}), 401
    if acct.unverified:
        return jsonify({"error": "Verify your email on Qiita first.", "unverified": True}), 403

    try:
        new_device = limiter.succeeded(email, device_token, client_ip, via_device)
    except limiter.LimiterUnavailable:
        new_device = None                                   # the login itself is fine
    purge_expired_sessions()
    user_id = resolve_user(email, acct.level)              # == acct.email: the lookup is exact
    raw_token, csrf_token = create_session(
        user_id=user_id, source="qiita-classic",
        password_fp=accounts.password_fingerprint(acct.password_hash),
        replace_session_hash=(g.get("session_row") or {}).get("session_hash"))
    resp = jsonify({**_identity_payload(get_user(user_id)), "csrf_token": csrf_token})
    _set_session_cookie(resp, raw_token)
    if new_device:
        resp.set_cookie(config.DEVICE_COOKIE_NAME, new_device,
                        max_age=limiter.DEVICE_TTL_SECONDS, **_cookie_kwargs())
    return resp


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    user_row = get_user(g.user_id) if g.get("user_id") is not None else None
    if user_row is None:
        return jsonify({"anonymous": True, "qiita_url": config.QIITA_CLASSIC_URL})
    payload = _identity_payload(user_row)
    payload["csrf_token"] = (g.get("session_row") or {}).get("csrf_token")
    payload["qiita_url"] = config.QIITA_CLASSIC_URL
    return jsonify(payload)


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    if g.get("session_row"):
        revoke_session(g.session_row["session_hash"])
    resp = jsonify({"ok": True})
    _clear_session_cookie(resp)
    return resp
