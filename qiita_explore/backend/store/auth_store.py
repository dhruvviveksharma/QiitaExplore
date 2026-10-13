"""Accounts + sessions for both logins (routes/auth_routes.py).

A QiitaExplore account is a `users` row with an opaque user_id, which owns all
its data, and the email both logins resolve through: classic Qiita and
qiita-miint reach the same account for the same email. Sessions are looked up
by SHA-256(raw token) — the plaintext session token never touches SQLite. A
classic session also carries a fingerprint of the account's password hash, so
a password changed on Qiita ends it (helpers/auth_middleware.py). No password
or qiita-miint token is ever stored.
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

class PrincipalMismatch(Exception):
    """The email's account belongs to a different qiita-miint principal: the
    address moved between qiita-miint accounts. Refused, so whoever holds the
    address now doesn't inherit someone else's data through qiita-miint."""


def resolve_user(email: str, *, level: str = None, principal_idx: int = None) -> str:
    """The account for a verified, normalized email — one per email, whichever
    login proved it (classic Qiita or qiita-miint), so both reach the same data.
    Matching is by email only: different emails are always different accounts.

      - A row with this email: that account. A classic login refreshes its
        level. A qiita-miint login records its principal on the account if it
        has none (and no other account holds it), and raises PrincipalMismatch
        if the account already has a different one.
      - Otherwise a new account with an opaque id.

    A concurrent login that wins a UNIQUE race (email or principal) makes this
    one fail; the retry then finds the winner's row."""
    try:
        return _resolve_user(email, level, principal_idx)
    except sqlite3.IntegrityError:
        return _resolve_user(email, level, principal_idx)


def _resolve_user(email, level, principal_idx) -> str:
    now = _now()
    with _conn() as conn:
        try:
            row = conn.execute("SELECT user_id, principal_idx FROM users WHERE email = ?", (email,)).fetchone()
            if row is None:
                held = principal_idx is not None and conn.execute(
                    "SELECT 1 FROM users WHERE principal_idx = ?", (principal_idx,)).fetchone() is not None
                user_id = "q" + secrets.token_hex(8)
                conn.execute(
                    "INSERT INTO users(user_id, email, user_level, principal_idx, created_at, updated_at, "
                    "last_login_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (user_id, email, level, None if held else principal_idx, now, now, now))
            else:
                user_id = row["user_id"]
                if principal_idx is not None and row["principal_idx"] not in (None, principal_idx):
                    raise PrincipalMismatch(user_id)
                conn.execute("UPDATE users SET user_level = COALESCE(?, user_level), updated_at = ?, "
                             "last_login_at = ? WHERE user_id = ?", (level, now, now, user_id))
                if principal_idx is not None and row["principal_idx"] is None:
                    held = conn.execute("SELECT 1 FROM users WHERE principal_idx = ?", (principal_idx,)).fetchone()
                    if held is None:
                        conn.execute("UPDATE users SET principal_idx = ? WHERE user_id = ?", (principal_idx, user_id))
            conn.commit()
            return user_id
        except (sqlite3.IntegrityError, PrincipalMismatch):
            conn.rollback()
            raise


def revoke_user_sessions(user_id: str):
    """End every live session of an account — whichever login made it."""
    with _conn() as conn:
        conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                     (_now(), user_id))
        conn.commit()


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
            "SELECT s.*, u.email FROM auth_sessions s LEFT JOIN users u USING (user_id) "
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
