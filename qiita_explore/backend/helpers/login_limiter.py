"""Brute-force limits for POST /api/auth/login (routes/auth_routes.py).

Classic Qiita has no rate limit and says whether an account exists, so these
limits protect QiitaExplore's availability more than the password itself. They
are built not to become a way to lock people out:

  - Per email: progressive delay, not a lock. The first 5 attempts in 15 min
    are free; each one after waits 2^(n-5) s (at most 5 min).
  - Per client: 30 failed attempts in 15 min blocks that client for 15 min,
    only when a trusted client-IP header is configured (config.CLIENT_IP_HEADER).
  - Known device: a browser that has logged into an account before carries a
    random device cookie (config.DEVICE_COOKIE_NAME). From that browser the
    account skips both limits above, so neither an attacker hammering the email
    nor one sharing the user's network IP (campus NAT) can keep the real user
    out of their usual browser (OWASP's device-cookie pattern). A device gets
    10 failures in a row, then the normal limits apply to it too.

An attempt is counted when it is reserved, before any bcrypt work, so parallel
requests can't slip past the count. A success clears its email's counter,
takes its own attempt back off the client's count (that one counts failures)
and resets the device's. Malformed attempts never get here. Keys are sha256 of
the normalized email or IP: no raw emails on disk. Storage is its own SQLite
file on local disk (config.LIMITER_DB_PATH), in a folder only this user can
write; if it can't be used, login answers 503 (fails closed).
"""
import hashlib
import math
import os
import secrets
import sqlite3
import time

import config

EMAIL_FREE_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60
MAX_DELAY_SECONDS = 5 * 60
CLIENT_MAX_ATTEMPTS = 30
CLIENT_BLOCK_SECONDS = 15 * 60
DEVICE_TTL_SECONDS = 30 * 24 * 3600
DEVICE_MAX_FAILURES = 10
_PRUNE_AFTER_SECONDS = 24 * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    key           TEXT PRIMARY KEY,
    window_start  REAL NOT NULL,
    count         INTEGER NOT NULL,
    blocked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS used_challenges (
    challenge_hash TEXT PRIMARY KEY,
    expires        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    token_hash TEXT PRIMARY KEY,
    email_key  TEXT NOT NULL,
    expires    REAL NOT NULL,
    failures   INTEGER NOT NULL DEFAULT 0
);
"""


class LimiterUnavailable(Exception):
    """The limiter's database can't be used; login must refuse (503)."""


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def email_key(email: str) -> str:
    return "e:" + _h(email)


def client_key(ip: str) -> str:
    return "c:" + _h(ip)


def _check_private(path):
    """The file and its folder must be this user's and not writable by anyone
    else: a planted or shared limiter file could hand out device rows (unlimited
    guesses) or wipe the counters."""
    if os.path.exists(path):
        st = os.stat(path)
        if st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise LimiterUnavailable(f"{path} must be owned by this user and not group/world-writable")


def _connect():
    path = config.LIMITER_DB_PATH
    try:
        os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
        _check_private(os.path.dirname(path) or ".")
        _check_private(path)
        conn = sqlite3.connect(path, timeout=1.0, isolation_level=None)
        os.chmod(path, 0o600)
        conn.execute("PRAGMA busy_timeout = 1000")
        conn.executescript(_SCHEMA)
        return conn
    except (OSError, sqlite3.Error) as exc:
        raise LimiterUnavailable(str(exc)) from exc


def _known_device(conn, device_token, ekey, now) -> bool:
    if not device_token:
        return False
    return conn.execute(
        "SELECT 1 FROM devices WHERE token_hash = ? AND email_key = ? AND expires > ? AND failures < ?",
        (_h(device_token), ekey, now, DEVICE_MAX_FAILURES)).fetchone() is not None


def _bump(conn, key, now):
    """Count one attempt on `key` (a fresh window after WINDOW_SECONDS). Returns the new count."""
    row = conn.execute("SELECT window_start, count FROM attempts WHERE key = ?", (key,)).fetchone()
    if row is None or now - row[0] > WINDOW_SECONDS:
        conn.execute("INSERT OR REPLACE INTO attempts(key, window_start, count, blocked_until) VALUES(?, ?, 1, 0)",
                     (key, now))
        return 1
    conn.execute("UPDATE attempts SET count = count + 1 WHERE key = ?", (key,))
    return row[1] + 1


def _blocked_for(conn, key, now) -> int:
    row = conn.execute("SELECT blocked_until FROM attempts WHERE key = ?", (key,)).fetchone()
    return max(0, math.ceil(row[0] - now)) if row else 0


def reserve(email: str, client_ip, device_token):
    """Reserve one login attempt. Returns (allowed, retry_after_seconds,
    via_device): via_device means a known device took it, outside the email and
    client limits — pass it on to succeeded(). Raises LimiterUnavailable."""
    now = time.time()
    ekey = email_key(email)
    ckey = client_key(client_ip) if client_ip else None
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if secrets.randbelow(50) == 0:      # prune now and then, inside the same lock
                conn.execute("DELETE FROM attempts WHERE window_start < ? AND blocked_until < ?",
                             (now - _PRUNE_AFTER_SECONDS, now))
                conn.execute("DELETE FROM devices WHERE expires < ?", (now,))
            if _known_device(conn, device_token, ekey, now):
                conn.execute("UPDATE devices SET failures = failures + 1 WHERE token_hash = ?",
                             (_h(device_token),))
                conn.execute("COMMIT")
                return True, 0, True
            for key in filter(None, (ckey, ekey)):
                wait = _blocked_for(conn, key, now)
                if wait:
                    conn.execute("COMMIT")
                    return False, wait, False
            if ckey and _bump(conn, ckey, now) >= CLIENT_MAX_ATTEMPTS:
                conn.execute("UPDATE attempts SET blocked_until = ? WHERE key = ?",
                             (now + CLIENT_BLOCK_SECONDS, ckey))
            n = _bump(conn, ekey, now)
            if n > EMAIL_FREE_ATTEMPTS:
                delay = min(2 ** (n - EMAIL_FREE_ATTEMPTS), MAX_DELAY_SECONDS)
                conn.execute("UPDATE attempts SET blocked_until = ? WHERE key = ?", (now + delay, ekey))
            conn.execute("COMMIT")
            return True, 0, False
        except Exception:
            conn.execute("ROLLBACK")
            raise
    except sqlite3.Error as exc:
        raise LimiterUnavailable(str(exc)) from exc
    finally:
        conn.close()


def succeeded(email: str, device_token, client_ip, via_device: bool):
    """A successful login: clear its email's counter, take this attempt back
    off the client's (which counts failures), and remember this browser for the
    account. Returns a new device token to set as the cookie, or None when the
    browser's current one (via_device, from reserve) stays valid."""
    now = time.time()
    ekey = email_key(email)
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM attempts WHERE key = ?", (ekey,))
            if client_ip and not via_device:
                conn.execute(
                    "UPDATE attempts SET count = MAX(count - 1, 0), "
                    "blocked_until = CASE WHEN count - 1 < ? THEN 0 ELSE blocked_until END WHERE key = ?",
                    (CLIENT_MAX_ATTEMPTS, client_key(client_ip)))
            if via_device:
                conn.execute("UPDATE devices SET failures = 0, expires = ? WHERE token_hash = ?",
                             (now + DEVICE_TTL_SECONDS, _h(device_token)))
                new_token = None
            else:
                if device_token:                 # one this account wore out, if any
                    conn.execute("DELETE FROM devices WHERE token_hash = ? AND email_key = ?",
                                 (_h(device_token), ekey))
                new_token = secrets.token_urlsafe(32)
                conn.execute("INSERT INTO devices(token_hash, email_key, expires) VALUES(?, ?, ?)",
                             (_h(new_token), ekey, now + DEVICE_TTL_SECONDS))
            conn.execute("COMMIT")
            return new_token
        except Exception:
            conn.execute("ROLLBACK")
            raise
    except sqlite3.Error as exc:
        raise LimiterUnavailable(str(exc)) from exc
    finally:
        conn.close()


# A used challenge is remembered this long past its expiry, so pruning can never
# race a late replay of it (and small backward clock steps don't matter).
_USED_CHALLENGE_MARGIN_SECONDS = 300


def consume_challenge(challenge: str, expires: float) -> str:
    """Mark a sign-in challenge used (helpers/login_crypto.py): "ok", "used" (a
    replayed request) or "expired". Decided under the write lock with one clock
    reading, so a replay sent just before expiry can't slip in after its row
    was pruned. Raises LimiterUnavailable."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            now = time.time()
            if expires < now:
                conn.execute("COMMIT")
                return "expired"
            conn.execute("DELETE FROM used_challenges WHERE expires < ?", (now - _USED_CHALLENGE_MARGIN_SECONDS,))
            cur = conn.execute("INSERT OR IGNORE INTO used_challenges(challenge_hash, expires) VALUES(?, ?)",
                               (_h(challenge), expires))
            conn.execute("COMMIT")
            return "ok" if cur.rowcount == 1 else "used"
        except Exception:
            conn.execute("ROLLBACK")
            raise
    except sqlite3.Error as exc:
        raise LimiterUnavailable(str(exc)) from exc
    finally:
        conn.close()


def reset_for_tests():
    """Empty its tables (tests share one limiter file per run)."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM attempts")
        conn.execute("DELETE FROM devices")
        conn.execute("DELETE FROM used_challenges")
    finally:
        conn.close()
