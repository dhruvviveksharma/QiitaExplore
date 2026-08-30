"""Structured per-turn lifecycle log — answers "when and where did the LLM
stop responding?" without grepping gunicorn noise.

One line per lifecycle event, pipe-delimited, greppable by chat_id:

    2026-08-30T18:22:01Z | chat=ab12cd34 | turn_start | scope=global model=minimax-m2 ...
    2026-08-30T18:22:34Z | chat=ab12cd34 | tool_fail | name=search_studies detail=malformed array literal...
    2026-08-30T18:22:59Z | chat=ab12cd34 | max_rounds_exhausted | rounds=7
    2026-08-30T18:23:04Z | chat=ab12cd34 | synthesis_empty |
    2026-08-30T18:23:04Z | chat=ab12cd34 | turn_done | chars=0 segments=9

Written to AGENT_TURN_LOG_FP (default: agent_turns.log next to the SQLite
store), rotating at 5MB × 3 backups. Failures to log never break a turn.
"""
import logging
import logging.handlers
import os
from datetime import datetime, timezone

_logger = None


def _get_logger():
    global _logger
    if _logger is not None:
        return _logger
    log = logging.getLogger("agent.turns")
    log.setLevel(logging.INFO)
    log.propagate = False  # keep these lines out of the gunicorn stream
    try:
        from store.db import DB_PATH
        default_fp = os.path.join(os.path.dirname(DB_PATH), "agent_turns.log")
        fp = os.getenv("AGENT_TURN_LOG_FP", default_fp)
        handler = logging.handlers.RotatingFileHandler(
            fp, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
    except Exception:
        log.addHandler(logging.NullHandler())
    _logger = log
    return log


def log_turn_event(chat_id, event, **fields):
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        detail = " ".join(f"{k}={str(v)[:200]}" for k, v in fields.items())
        _get_logger().info(f"{ts} | chat={chat_id} | {event} | {detail}")
    except Exception:
        pass  # logging must never break a turn
