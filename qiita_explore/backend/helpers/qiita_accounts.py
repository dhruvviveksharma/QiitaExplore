"""Classic-Qiita accounts: the login check behind POST /api/auth/login
(routes/auth_routes.py) and the session re-checks (helpers/auth_middleware.py).

QiitaExplore reads qiita.qiita_user through its read-only Postgres role and
checks the password the way classic Qiita does (qiita_db/user.py User.login;
qiita_db/util.py hash_password: bcrypt with the stored hash as salt), with a
constant-time compare. Sign-up, email verification and password reset stay on
classic Qiita (QIITA_CLASSIC_URL) — this role can't write.

Account reads use their own small pool, separate from helpers/pg_pool.py: short
connect and statement timeouts, read-only transactions, and a circuit breaker,
so a silent Postgres never pins the login path or the per-request re-check.
Passwords and hashes are never logged.
"""
import hashlib
import hmac
import logging
import re
import threading
import time
from dataclasses import dataclass

import bcrypt
import psycopg2
from psycopg2.pool import ThreadedConnectionPool

from helpers.emails import normalize_email  # noqa: F401 — part of this module's API (routes use accounts.normalize_email)

logger = logging.getLogger(__name__)

UNVERIFIED_LEVEL_ID = 5          # classic Qiita refuses this id (qiita_db/user.py "MAGIC NUMBER 5")
_MAX_PASSWORD = 1024             # QiitaExplore's own cap; classic Qiita sets none
_BCRYPT_MAX_BYTES = 72           # bcrypt 4.x truncates here (as classic Qiita does); 5.x would raise
_HASH_RE = re.compile(r"^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")

_ACCOUNT_SQL = """
    SELECT q.email, q.password, q.user_level_id, ul.name
    FROM qiita.qiita_user q JOIN qiita.user_level ul USING (user_level_id)
    WHERE q.email = %s
"""


class AccountsUnavailable(Exception):
    """Qiita's user table can't be read right now (Postgres down, slow, or the breaker is open)."""


class Busy(Exception):
    """This worker is already checking a password; try again in a moment."""


@dataclass(frozen=True)
class Account:
    email: str              # as stored in Qiita
    password_hash: str
    level_id: int
    level: str

    @property
    def unverified(self) -> bool:
        return self.level_id == UNVERIFIED_LEVEL_ID or self.level == "unverified"


def valid_password_format(pw) -> bool:
    """Classic Qiita's rule (qiita_db/user.py validate_password: 8+ characters,
    ASCII, no space or backslash) plus our own length cap."""
    return (isinstance(pw, str) and 8 <= len(pw) <= _MAX_PASSWORD and pw.isascii()
            and " " not in pw and "\\" not in pw)


# ── reading accounts ────────────────────────────────────────────────────────

_pool = None
_pool_lock = threading.Lock()
_BREAKER_FAILS = 3
_BREAKER_OPEN_SECONDS = 30
_breaker = {"fails": 0, "open_until": 0.0}
_breaker_lock = threading.Lock()


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            from qiita_core.qiita_settings import qiita_config
            # min 2 (a worker's threads) stay open for reuse; psycopg2 closes
            # any connection returned beyond minconn.
            _pool = ThreadedConnectionPool(
                2, 4,
                user=qiita_config.user, password=qiita_config.password,
                database=qiita_config.database, host=qiita_config.host, port=qiita_config.port,
                connect_timeout=5, keepalives=1, keepalives_idle=30, keepalives_interval=10,
                keepalives_count=3,
                options="-c statement_timeout=5000 -c default_transaction_read_only=on",
            )
        return _pool


def _trip(failed: bool):
    with _breaker_lock:
        if not failed:
            _breaker["fails"] = 0
            return
        _breaker["fails"] += 1
        if _breaker["fails"] >= _BREAKER_FAILS:
            _breaker["open_until"] = time.monotonic() + _BREAKER_OPEN_SECONDS
            _breaker["fails"] = 0


def fetch_account(email: str):
    """Account for a normalized email, or None. Raises AccountsUnavailable."""
    if time.monotonic() < _breaker["open_until"]:
        raise AccountsUnavailable("circuit open")
    pool = conn = None
    try:
        pool = _get_pool()
        conn = pool.getconn()
        with conn.cursor() as cur:
            cur.execute(_ACCOUNT_SQL, (email,))
            row = cur.fetchone()
        conn.rollback()                      # end the read-only transaction
        pool.putconn(conn)
        conn = None
    except (psycopg2.Error, OSError) as exc:
        if conn is not None and pool is not None:
            pool.putconn(conn, close=True)
        _trip(True)
        logger.warning("Qiita account lookup failed: %s", type(exc).__name__)
        raise AccountsUnavailable(type(exc).__name__) from exc
    _trip(False)
    if row is None:
        return None
    return Account(email=row[0], password_hash=row[1] or "", level_id=int(row[2]), level=row[3] or "")


# ── checking passwords ──────────────────────────────────────────────────────

# One bcrypt check per worker at a time (gunicorn runs 2 threads per worker):
# a login flood can never take both, and a second login waits at most 0.5 s.
_bcrypt_slot = threading.Semaphore(1)
_BCRYPT_WAIT_SECONDS = 0.5
_dummy = {"hash": None}


def _dummy_hash() -> bytes:
    # Lazy: a cost-12 hash takes ~0.25 s, which every test re-import would pay.
    if _dummy["hash"] is None:
        _dummy["hash"] = bcrypt.hashpw(b"qiita-explore-no-such-account", bcrypt.gensalt(12))
    return _dummy["hash"]


def _pw_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def check_password(password: str, stored_hash: str) -> bool:
    """bcrypt compare (constant time). A stored hash that isn't a bcrypt hash
    returns False, after the same work as a real check. Raises Busy."""
    if not _bcrypt_slot.acquire(timeout=_BCRYPT_WAIT_SECONDS):
        raise Busy()
    try:
        if not isinstance(stored_hash, str) or not _HASH_RE.match(stored_hash):
            logger.warning("Qiita account has a password hash QiitaExplore can't read")
            bcrypt.checkpw(_pw_bytes(password), _dummy_hash())
            return False
        return bcrypt.checkpw(_pw_bytes(password), stored_hash.encode("ascii"))
    finally:
        _bcrypt_slot.release()


def burn_check(password: str) -> bool:
    """The work of one password check, for an email with no account, so its
    timing matches a wrong password. Always False. Raises Busy."""
    if not _bcrypt_slot.acquire(timeout=_BCRYPT_WAIT_SECONDS):
        raise Busy()
    try:
        bcrypt.checkpw(_pw_bytes(password), _dummy_hash())
        return False
    finally:
        _bcrypt_slot.release()


def password_fingerprint(stored_hash: str) -> str:
    """sha256 of the stored bcrypt hash: kept on the session to notice a password
    change on Qiita. Useless for cracking (it hides the salt)."""
    return hashlib.sha256((stored_hash or "").encode("utf-8")).hexdigest()


def same_fingerprint(a, b) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a, b)
