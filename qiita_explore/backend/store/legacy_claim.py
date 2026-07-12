"""Atomic one-time claim of legacy 'default'-owned rows to a configured Qiita
principal. Disabled unless QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX is set —
even then, only that exact principal may claim, and only once. Never auto-claims.
"""

import sqlite3

import config

from .db import _conn, _now

_LEGACY_USER_ID = "default"
_CLAIM_META_KEY = "default_claimed_by"
_CLAIM_AT_META_KEY = "default_claimed_at"

# The 5 root ownership columns (child rows inherit ownership through these).
_CLAIMABLE_TABLES = (
    "projects",
    "project_chats",
    "global_chats",
    "merge_workspaces",
    "merge_jobs",
)


class LegacyClaimConflict(Exception):
    """Raised when a claim attempt loses a race against an already-completed claim."""


def _already_claimed() -> bool:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (_CLAIM_META_KEY,)).fetchone()
    return row is not None


def claim_eligible(user_id: str) -> bool:
    """True only for the exact configured claimant, and only before the first
    successful claim."""
    claimant_idx = config.QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX
    if claimant_idx is None:
        return False
    if user_id != str(claimant_idx):
        return False
    return not _already_claimed()


def legacy_default_counts() -> dict:
    """Row counts currently owned by the legacy 'default' user, for the claim
    confirmation UI."""
    with _conn() as conn:
        return {
            tbl: conn.execute(
                f"SELECT COUNT(1) FROM {tbl} WHERE user_id = ?", (_LEGACY_USER_ID,)
            ).fetchone()[0]
            for tbl in _CLAIMABLE_TABLES
        }


def claim_legacy_default(user_id: str) -> dict:
    """Atomically reassign every 'default'-owned row to user_id.

    Raises ValueError if not eligible (wrong principal, unset config, or
    already claimed), or LegacyClaimConflict if a concurrent request wins the
    race for the meta marker — the whole transaction rolls back in that case,
    so a losing attempt never leaves a partial reassignment.
    """
    if not claim_eligible(user_id):
        raise ValueError("not eligible to claim")

    try:
        with _conn() as conn:
            counts = {}
            for tbl in _CLAIMABLE_TABLES:
                cur = conn.execute(
                    f"UPDATE {tbl} SET user_id = ? WHERE user_id = ?",
                    (user_id, _LEGACY_USER_ID),
                )
                counts[tbl] = cur.rowcount
            # meta.key is a PRIMARY KEY — a second racing claim raises
            # IntegrityError here and the whole transaction rolls back.
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?)",
                (_CLAIM_META_KEY, user_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                (_CLAIM_AT_META_KEY, _now()),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise LegacyClaimConflict("legacy data already claimed") from None
    return counts
