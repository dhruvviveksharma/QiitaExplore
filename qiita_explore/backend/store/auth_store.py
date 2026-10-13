"""Accounts + sessions for classic-Qiita login (routes/auth_routes.py).

A QiitaExplore account is a `users` row bound to a classic-Qiita email
(qiita_email). Sessions are looked up by SHA-256(raw token) — the plaintext
session token never touches SQLite — and carry a fingerprint of the account's
password hash, so a password changed on Qiita ends them
(helpers/auth_middleware.py). No password is ever stored.
"""

import hashlib
import secrets
import sqlite3
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

def resolve_user(email: str, level: str) -> str:
    """The user_id of the account bound to a classic-Qiita email (normalized),
    creating an unbound-to-anything-else new one with an opaque id on first
    login. Two concurrent first logins: the loser's INSERT hits the UNIQUE
    constraint and re-reads the winner's row."""
    now = _now()
    with _conn() as conn:
        row = conn.execute("SELECT user_id FROM users WHERE qiita_email = ?", (email,)).fetchone()
        if row is not None:
            conn.execute("UPDATE users SET user_level = ?, updated_at = ?, last_login_at = ? WHERE user_id = ?",
                         (level, now, now, row["user_id"]))
            conn.commit()
            return row["user_id"]
        user_id = "q" + secrets.token_hex(8)
        try:
            conn.execute(
                "INSERT INTO users(user_id, qiita_email, user_level, created_at, updated_at, last_login_at) "
                "VALUES(?, ?, ?, ?, ?, ?)", (user_id, email, level, now, now, now))
            conn.commit()
            return user_id
        except sqlite3.IntegrityError:
            conn.rollback()
            row = conn.execute("SELECT user_id FROM users WHERE qiita_email = ?", (email,)).fetchone()
            if row is None:
                raise
            return row["user_id"]


def bind_account(user_id: str, email: str) -> bool:
    """Bind an existing (qiita-miint era) account to a classic-Qiita email —
    only for scripts/bind_classic_accounts.py, after the operator confirmed it.
    False when the account is missing or already bound, or the email is taken."""
    with _conn() as conn:
        taken = conn.execute("SELECT 1 FROM users WHERE qiita_email = ?", (email,)).fetchone()
        if taken is not None:
            return False
        cur = conn.execute("UPDATE users SET qiita_email = ?, updated_at = ? WHERE user_id = ? AND qiita_email IS NULL",
                           (email, _now(), user_id))
        conn.commit()
        return cur.rowcount == 1


def get_user(user_id: str):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return _as_dict(row)


# ── Sessions ─────────────────────────────────────────────────────────────────

def create_session(*, user_id: str, source: str, password_fp: str, replace_session_hash: str = None):
    """Create a new session row, verified as of now. Returns (raw_token, csrf_token).

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
                last_verified_at, absolute_expires_at, revoked_at, password_fp
            ) VALUES(?, ?, '', NULL, ?, NULL, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                session_hash, user_id, source, csrf_token, now, now, now,
                absolute_expires_at, password_fp,
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
            "SELECT s.*, u.qiita_email FROM auth_sessions s LEFT JOIN users u USING (user_id) "
            "WHERE s.session_hash = ?", (session_hash,)
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


def mark_session_verified(session_hash: str):
    """The account was re-checked against Qiita just now (helpers/auth_middleware.py)."""
    with _conn() as conn:
        conn.execute("UPDATE auth_sessions SET last_verified_at = ? WHERE session_hash = ?",
                     (_now(), session_hash))
        conn.commit()


def revoke_session(session_hash: str):
    with _conn() as conn:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE session_hash = ? AND revoked_at IS NULL",
            (_now(), session_hash),
        )
        conn.commit()


_REVOKED_KEEP_DAYS = 7


def purge_expired_sessions():
    """Hard-delete sessions past absolute expiry, and revoked ones after a
    week (kept that long for audit). Run on each login (TKT-056)."""
    kept_since = (datetime.utcnow() - timedelta(days=_REVOKED_KEEP_DAYS)).isoformat() + "Z"
    with _conn() as conn:
        conn.execute(
            "DELETE FROM auth_sessions WHERE absolute_expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)",
            (_now(), kept_since),
        )
        conn.commit()
