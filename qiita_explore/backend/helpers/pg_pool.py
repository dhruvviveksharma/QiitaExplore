"""Shared, thread-safe Postgres connection pool for read-only Qiita queries.

Bypasses the qiita_db.sql_connection.TRN singleton, which is a single shared
psycopg2 connection not safe for concurrent use across gthread worker threads
(see helpers/sample_search.py, which established this same workaround for its
own deep-search path).
"""

import threading

from psycopg2.pool import ThreadedConnectionPool
from qiita_core.qiita_settings import qiita_config

from config import PG_POOL_MIN_CONN, PG_POOL_MAX_CONN

_pool = None
_pool_lock = threading.Lock()


def get_pool():
    """Lazily create the module-level connection pool (fork-safe: built on first use,
    not at import time, so gunicorn worker forks never share connections)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(
                    PG_POOL_MIN_CONN, PG_POOL_MAX_CONN,
                    user=qiita_config.user,
                    password=qiita_config.password,
                    database=qiita_config.database,
                    host=qiita_config.host,
                    port=qiita_config.port,
                )
    return _pool


def pooled_fetchall(sql, params=None):
    """Run a single read-only SELECT on a dedicated pooled connection; return all rows."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            return cur.fetchall()
    finally:
        pool.putconn(conn)
