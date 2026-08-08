"""User + session CRUD for Qiita-identity (paste-PAT) authentication.

Sessions are looked up by SHA-256(raw token) — the plaintext session token
never touches SQLite. The Qiita PAT is verified once at login and is not
stored.
"""

import hashlib
import json
import secrets
from datetime import datetime, timedelta

import config

from .db import _conn, _as_dict, _now

_SESSION_TOKEN_BYTES = 32
_CSRF_TOKEN_BYTES = 32


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.rstrip("Z"))


# ── Users ────────────────────────────────────────────────────────────────────

def upsert_user(*, principal_idx, email, system_role, scopes, profile_complete) -> str:
    """Insert or refresh a users row keyed by str(principal_idx). Returns user_id."""
    user_id = str(principal_idx)
    now = _now()
    scopes_json = json.dumps(scopes or [])
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, principal_idx, email, system_role, scopes,
                               profile_complete, created_at, updated_at, last_login_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                email            = excluded.email,
                system_role      = excluded.system_role,
                scopes           = excluded.scopes,
                profile_complete = excluded.profile_complete,
                updated_at       = excluded.updated_at,
                last_login_at    = excluded.last_login_at
            """,
            (
                user_id, int(principal_idx), email, system_role, scopes_json,
                1 if profile_complete else 0, now, now, now,
            ),
        )
        conn.commit()
    return user_id


def get_user(user_id: str):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return _as_dict(row)


# ── Sessions ─────────────────────────────────────────────────────────────────

def create_session(*, user_id: str, source: str, replace_session_hash: str = None,
                    token_idx: str = None, pat_expires_at: str = None):
    """Create a new session row. Returns (raw_token, csrf_token).

    When replace_session_hash is set, the prior session is revoked in the same
    transaction — used when a user signs in again while already authenticated.
    """
    raw_token = secrets.token_urlsafe(_SESSION_TOKEN_BYTES)
    csrf_token = secrets.token_urlsafe(_CSRF_TOKEN_BYTES)
    session_hash = _hash_token(raw_token)
    now_dt = datetime.utcnow()
    now = now_dt.isoformat() + "Z"
    absolute_expires_at = (
        now_dt + timedelta(seconds=config.AUTH_SESSION_ABSOLUTE_TTL_SECONDS)
    ).isoformat() + "Z"
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO auth_sessions(
                session_hash, user_id, pat_encrypted, token_idx, source,
                pat_expires_at, csrf_token, created_at, last_seen_at,
                last_verified_at, absolute_expires_at, revoked_at
            ) VALUES(?, ?, '', ?, ?, ?, ?, ?, ?, NULL, ?, NULL)
            """,
            (
                session_hash, user_id, token_idx, source,
                pat_expires_at, csrf_token, now, now, absolute_expires_at,
            ),
        )
        if replace_session_hash:
            conn.execute(
                """
                UPDATE auth_sessions SET revoked_at = ?
                WHERE session_hash = ? AND revoked_at IS NULL
                """,
                (now, replace_session_hash),
            )
        conn.commit()
    return raw_token, csrf_token


def get_session_by_token(raw_token: str):
    """Return the session row for a raw token, or None if missing, revoked, or
    past its absolute expiry.

    There is deliberately no idle check: sitting unused is not a reason to sign
    someone out, and under a 24-hour ceiling it only cost mid-session logouts.
    """
    session_hash = _hash_token(raw_token)
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM auth_sessions WHERE session_hash = ?", (session_hash,)
        ).fetchone()
    if row is None:
        return None
    sess = _as_dict(row)
    if sess.get("revoked_at"):
        return None

    try:
        if _parse_iso(sess["absolute_expires_at"]) < datetime.utcnow():
            return None
    except (KeyError, ValueError):
        return None
    return sess


# last_seen_at is informational now that nothing expires on it, so it is not
# worth an UPDATE per request — that was 8 concurrent writers against one WAL
# file under `gunicorn -w 4 --threads 2`.
_TOUCH_THROTTLE_SECONDS = 300


def touch_session(session_hash: str, last_seen_at: str = None):
    """Refresh last_seen_at if it is stale — never extends absolute_expires_at."""
    if last_seen_at:
        try:
            if (datetime.utcnow() - _parse_iso(last_seen_at)).total_seconds() < _TOUCH_THROTTLE_SECONDS:
                return
        except ValueError:
            pass
    with _conn() as conn:
        conn.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE session_hash = ?",
            (_now(), session_hash),
        )
        conn.commit()


def revoke_session(session_hash: str):
    with _conn() as conn:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE session_hash = ? AND revoked_at IS NULL",
            (_now(), session_hash),
        )
        conn.commit()


def purge_expired_sessions():
    """Hard-delete sessions past absolute expiry (revoked rows are kept for audit)."""
    with _conn() as conn:
        conn.execute(
            "DELETE FROM auth_sessions WHERE absolute_expires_at < ? AND revoked_at IS NULL",
            (_now(),),
        )
        conn.commit()
