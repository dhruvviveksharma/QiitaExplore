"""User + session CRUD for Qiita-identity (paste-PAT) authentication.

Sessions are looked up by SHA-256(raw token) — the plaintext token never
touches SQLite. The PAT itself is stored Fernet-encrypted (helpers/pat_crypto).
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

def create_session(*, user_id: str, pat_encrypted: str, source: str,
                    token_idx: str = None, pat_expires_at: str = None):
    """Create a new session row. Returns (raw_token, csrf_token)."""
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
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                session_hash, user_id, pat_encrypted, token_idx, source,
                pat_expires_at, csrf_token, now, now, now, absolute_expires_at,
            ),
        )
        conn.commit()
    return raw_token, csrf_token


def get_session_by_token(raw_token: str):
    """Return the session row for a raw token, or None if missing, revoked,
    idle-expired, or past its absolute expiry."""
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

    now = datetime.utcnow()
    try:
        if _parse_iso(sess["absolute_expires_at"]) < now:
            return None
        idle_deadline = _parse_iso(sess["last_seen_at"]) + timedelta(
            seconds=config.AUTH_SESSION_IDLE_TTL_SECONDS
        )
        if idle_deadline < now:
            return None
    except (KeyError, ValueError):
        return None
    return sess


def touch_session(session_hash: str):
    """Update last_seen_at only — must never extend absolute_expires_at."""
    with _conn() as conn:
        conn.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE session_hash = ?",
            (_now(), session_hash),
        )
        conn.commit()


def mark_session_verified(session_hash: str):
    with _conn() as conn:
        conn.execute(
            "UPDATE auth_sessions SET last_verified_at = ? WHERE session_hash = ?",
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
