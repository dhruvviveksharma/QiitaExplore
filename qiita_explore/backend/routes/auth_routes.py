import json
import logging

from flask import g, jsonify, request

import config
from run import app
from helpers.pat_crypto import encrypt_pat, PatCryptoError
from helpers.qiita_client import whoami
from store.auth_store import create_session, get_user, revoke_session, upsert_user
from store.legacy_claim import (
    LegacyClaimConflict,
    claim_eligible,
    claim_legacy_default,
    legacy_default_counts,
)

logger = logging.getLogger(__name__)


def _cookie_kwargs():
    return dict(httponly=True, samesite="Lax", secure=config.SESSION_COOKIE_SECURE, path="/")


def _set_session_cookie(resp, raw_token: str):
    resp.set_cookie(
        config.SESSION_COOKIE_NAME,
        raw_token,
        max_age=config.AUTH_SESSION_ABSOLUTE_TTL_SECONDS,
        **_cookie_kwargs(),
    )


def _clear_session_cookie(resp):
    resp.delete_cookie(config.SESSION_COOKIE_NAME, path="/")


def _origin_allowed() -> bool:
    """Exact-origin check for the connect endpoint (defense against a
    cross-site page silently posting a token to us on the user's behalf)."""
    if not config.ALLOWED_ORIGINS:
        # No explicit allowlist configured — same-origin production
        # deployment behind the proxy. Nothing to check against.
        return True
    return request.headers.get("Origin") in config.ALLOWED_ORIGINS


def _identity_payload(user_row: dict) -> dict:
    return {
        "user_id": user_row["user_id"],
        "email": user_row.get("email"),
        "system_role": user_row.get("system_role"),
        "scopes": json.loads(user_row.get("scopes") or "[]"),
        "profile_complete": bool(user_row.get("profile_complete")),
        "claim_eligible": claim_eligible(user_row["user_id"]),
    }


@app.route("/api/auth/login-url", methods=["GET"])
def api_auth_login_url():
    return jsonify({"url": f"{config.QIITA_PUBLIC_LOGIN_URL}/api/v1/auth/login"})


@app.route("/api/auth/connect", methods=["POST"])
def api_auth_connect():
    if not _origin_allowed():
        return jsonify({"error": "origin not allowed"}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON body required"}), 400
    pat = (data.get("token") or "").strip()
    if not pat:
        return jsonify({"error": "token required"}), 400

    result = whoami(pat)
    if result.transient_error:
        return jsonify({"error": "Qiita is temporarily unreachable, try again shortly"}), 503
    if not result.ok:
        return jsonify({"error": "invalid or unrecognized Qiita token"}), 401

    identity = result.identity
    principal_idx = identity.get("principal_idx")
    if principal_idx is None:
        return jsonify({"error": "unexpected identity response from Qiita"}), 502

    try:
        user_id = upsert_user(
            principal_idx=principal_idx,
            email=identity.get("email"),
            system_role=identity.get("system_role"),
            scopes=identity.get("scopes") or [],
            profile_complete=identity.get("profile_complete", False),
        )
        pat_encrypted = encrypt_pat(pat)
        raw_token, csrf_token = create_session(
            user_id=user_id,
            pat_encrypted=pat_encrypted,
            source="paste",
        )
        user_row = get_user(user_id)
    except PatCryptoError:
        logger.exception("PAT encryption failed — QIITA_EXPLORE_PAT_ENCRYPTION_KEY misconfigured")
        return jsonify({"error": "server misconfiguration"}), 500
    except Exception as exc:
        logger.exception("unexpected error establishing session for principal_idx=%s", principal_idx)
        body = {"error": "unexpected error establishing session"}
        if config.DEBUG_ERROR_DETAIL:
            body["detail"] = f"{type(exc).__name__}: {exc}"
        return jsonify(body), 500

    resp = jsonify({**_identity_payload(user_row), "csrf_token": csrf_token})
    _set_session_cookie(resp, raw_token)
    return resp


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    if g.get("user_id") is None:
        return jsonify({"anonymous": True})
    user_row = get_user(g.user_id)
    if user_row is None:
        return jsonify({"anonymous": True})
    payload = _identity_payload(user_row)
    payload["csrf_token"] = (g.session_row or {}).get("csrf_token")
    return jsonify(payload)


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    if g.get("session_row"):
        revoke_session(g.session_row["session_hash"])
    resp = jsonify({"ok": True})
    _clear_session_cookie(resp)
    return resp


@app.route("/api/auth/legacy-default", methods=["GET"])
def api_auth_legacy_default():
    eligible = claim_eligible(g.user_id)
    payload = {"eligible": eligible}
    if eligible:
        payload["counts"] = legacy_default_counts()
    return jsonify(payload)


@app.route("/api/auth/claim-default", methods=["POST"])
def api_auth_claim_default():
    try:
        counts = claim_legacy_default(g.user_id)
    except ValueError:
        return jsonify({"error": "not eligible to claim"}), 403
    except LegacyClaimConflict:
        return jsonify({"error": "legacy data already claimed"}), 409
    return jsonify({"ok": True, "claimed": counts})
