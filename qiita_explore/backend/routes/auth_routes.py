"""Two ways in, one account per email (docs/02-authentication.md):

  POST /api/auth/login    classic-Qiita email + password, checked against
                          qiita.qiita_user (helpers/qiita_accounts.py)
  POST /api/auth/connect  a qiita-miint `qk_` token, checked once with whoami
                          (helpers/qiita_client.py); GET /api/auth/login-url
                          gives the qiita-miint login page that issues it
  GET  /api/auth/me, POST /api/auth/logout

Both resolve the verified email to the same account (store/auth_store.py
resolve_user). Neither secret travels readable: the browser seals it to this
backend's key (GET /api/auth/login-key, helpers/login_crypto.py), and a plain
password or token field is refused. Each method can be switched off (config.LOGIN_METHODS); its
routes then answer 404. Every check fails closed. For login, in this order:
body size, JSON only (stops HTML-form login CSRF), exact Origin, format,
brute-force limits (helpers/login_limiter.py), then the account and the bcrypt
compare; unknown email and wrong password answer identically. Neither the
password nor the token is ever logged, stored, put in an error or echoed back.
"""
import ipaddress
import json
import logging
import re
from urllib.parse import quote

from flask import abort, g, jsonify, request

import config
from run import app
from helpers import login_crypto
from helpers import login_limiter as limiter
from helpers import qiita_accounts as accounts
from helpers import qiita_client
from helpers.qiita_client import whoami
from store.auth_store import (PrincipalMismatch, create_session, get_user, purge_expired_sessions, resolve_user,
                              revoke_session)

logger = logging.getLogger(__name__)

_MAX_LOGIN_BODY = 8 * 1024
_GENERIC_401 = "Incorrect email or password."
# qk_ + secrets.token_urlsafe(32) (qiita-control-plane auth/token.py); bounded so
# junk never reaches whoami.
_TOKEN_RE = re.compile(r"qk_[A-Za-z0-9_\-]{30,128}")


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
        "email": user_row.get("email"),
        "level": user_row.get("user_level"),
    }


def _require_method(method: str):
    if method not in config.LOGIN_METHODS:
        abort(404)


def _read_json_body():
    """The checks login and connect share, in order: size, JSON only, exact
    Origin, then a bounded read (a chunked body has no Content-Length).
    Returns (dict, None) or (None, (response, status))."""
    if (request.content_length or 0) > _MAX_LOGIN_BODY:
        return None, (jsonify({"error": "request too large"}), 413)
    if not request.is_json:
        return None, (jsonify({"error": "JSON body required"}), 415)
    err = _origin_error()
    if err:
        return None, (jsonify(err[0]), err[1])
    raw = request.stream.read(_MAX_LOGIN_BODY + 1)
    if len(raw) > _MAX_LOGIN_BODY:
        return None, (jsonify({"error": "request too large"}), 413)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None, (jsonify({"error": "JSON body required"}), 415)
    return data, None


def _start_session(user_id: str, source: str, password_fp=None):
    """A fresh session for user_id, replacing the browser's current one."""
    purge_expired_sessions()
    raw_token, csrf_token = create_session(
        user_id=user_id, source=source, password_fp=password_fp,
        replace_session_hash=(g.get("session_row") or {}).get("session_hash"))
    resp = jsonify({**_identity_payload(get_user(user_id)), "csrf_token": csrf_token})
    _set_session_cookie(resp, raw_token)
    return resp


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


def _signin_unavailable():
    return jsonify({"error": "Sign-in is unavailable right now — try again shortly."}), 503


def _open_sealed(data: dict, secret_field: str):
    """The payload the browser sealed (helpers/login_crypto.py), or an error
    response. The secret sent in the clear is refused outright, so neither an
    old page nor a script can put it on the wire readable. None of this is
    counted toward a login limit: no password or token has been tried yet."""
    if secret_field in data:
        return None, (jsonify({"error": "This sign-in page is out of date — reload it."}), 400)
    try:
        payload = login_crypto.open_envelope(data.get("envelope"))
    except login_crypto.EnvelopeExpired:
        return None, (jsonify({"error": "The sign-in form expired — try again.", "expired": True}), 409)
    except login_crypto.EnvelopeInvalid:
        return None, (jsonify({"error": "The sign-in request couldn't be read — reload the page and try again."}),
                      400)
    except login_crypto.Busy:
        return None, _busy()
    except (login_crypto.KeyUnavailable, limiter.LimiterUnavailable):
        logger.exception("sealed sign-in unavailable")
        return None, _signin_unavailable()
    return payload, None


@app.route("/api/auth/login-key", methods=["GET"])
def api_auth_login_key():
    """The public key and a one-time challenge the sign-in forms seal to."""
    try:
        resp = jsonify(login_crypto.public_info())
    except login_crypto.KeyUnavailable:
        logger.exception("login key unavailable")
        return _signin_unavailable()
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    _require_method("classic")
    data, err = _read_json_body()
    if err:
        return err
    sealed, err = _open_sealed(data, "password")
    if err:
        return err

    email = accounts.normalize_email(sealed.get("email"))
    password = sealed.get("password")
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
    user_id = resolve_user(email, level=acct.level)        # == acct.email: the lookup is exact
    resp = _start_session(user_id, "qiita-classic", accounts.password_fingerprint(acct.password_hash))
    if new_device:
        resp.set_cookie(config.DEVICE_COOKIE_NAME, new_device,
                        max_age=limiter.DEVICE_TTL_SECONDS, **_cookie_kwargs())
    return resp


@app.route("/api/auth/login-url", methods=["GET"])
def api_auth_login_url():
    """The qiita-miint login page that issues a `qk_` token. With LoginRocket
    configured it goes through /logout first, so a cached AuthRocket session
    can't complete the login as whoever signed in last."""
    _require_method("miint")
    url = f"{config.QIITA_PUBLIC_LOGIN_URL}/api/v1/auth/login"
    if config.QIITA_LOGINROCKET_URL:
        url = f"{config.QIITA_LOGINROCKET_URL}/logout?redirect_uri={quote(url, safe='')}"
    return jsonify({"url": url})


@app.route("/api/auth/connect", methods=["POST"])
def api_auth_connect():
    """Sign in with a qiita-miint token. whoami proves it once; the token itself
    is never stored. A failed connect leaves the browser's current session alone."""
    _require_method("miint")
    data, err = _read_json_body()
    if err:
        return err
    sealed, err = _open_sealed(data, "token")
    if err:
        return err
    token = sealed.get("token")
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token.strip()):
        return jsonify({"error": "That isn't a Qiita-Miint access token (qk_…)."}), 401
    try:
        result = whoami(token.strip())
    except qiita_client.Busy:
        return _busy()
    if result.transient_error:
        return jsonify({"error": "Qiita-Miint is unreachable — try again shortly."}), 503
    if not result.ok:
        return jsonify({"error": "invalid or unrecognized Qiita token"}), 401
    identity = result.identity
    principal_idx = identity.get("principal_idx")
    if isinstance(principal_idx, bool) or not isinstance(principal_idx, int):
        return jsonify({"error": "unexpected identity response from Qiita"}), 502
    # The same rule as classic login: email is how both logins reach one account,
    # so a non-ASCII lookalike must never resolve onto someone else's address.
    # It's identity data from a server, so padding is refused, not trimmed.
    raw_email = identity.get("email")
    email = accounts.normalize_email(raw_email)
    if email is None or raw_email != raw_email.strip():
        return jsonify({"error": "This Qiita-Miint account's email can't be used to sign in here."}), 401
    try:
        user_id = resolve_user(email, principal_idx=principal_idx)
    except PrincipalMismatch as exc:
        logger.warning("qiita-miint principal %s refused: account %s belongs to another principal", principal_idx, exc)
        return jsonify({"error": "This email's QiitaExplore account belongs to a different Qiita-Miint account. "
                                 "Sign in with classic Qiita instead."}), 409
    return _start_session(user_id, "qiita-miint")


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    user_row = get_user(g.user_id) if g.get("user_id") is not None else None
    if user_row is None:
        payload = {"anonymous": True}
    else:
        payload = _identity_payload(user_row)
        payload["csrf_token"] = (g.get("session_row") or {}).get("csrf_token")
    payload["qiita_url"] = config.QIITA_CLASSIC_URL
    payload["login_methods"] = list(config.LOGIN_METHODS)
    return jsonify(payload)


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    if g.get("session_row"):
        revoke_session(g.session_row["session_hash"])
    resp = jsonify({"ok": True})
    _clear_session_cookie(resp)
    return resp
