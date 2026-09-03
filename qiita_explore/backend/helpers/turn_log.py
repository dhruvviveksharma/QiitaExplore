"""Structured per-turn lifecycle log — answers "when and where did the LLM
stop responding?" without grepping gunicorn noise.

One line per lifecycle event, pipe-delimited, greppable by chat_id:

    2026-08-30T18:22:01Z | chat=ab12cd34 | turn_start | scope=global model=minimax-m2 ...
    2026-08-30T18:22:34Z | chat=ab12cd34 | tool_fail | name=search_studies detail=malformed array literal...
    2026-08-30T18:22:59Z | chat=ab12cd34 | max_rounds_exhausted | rounds=7
    2026-08-30T18:23:04Z | chat=ab12cd34 | synthesis_empty |
    2026-08-30T18:23:04Z | chat=ab12cd34 | turn_done | chars=0 segments=9

Written to AGENT_TURN_LOG_FP (default: agent_turns.log next to the SQLite
store), append-only — NOT size-rotated: 4 gunicorn workers share this file,
and stdlib size-based rotation is not multi-process safe (a worker's
rollover renames the file out from under its siblings, clobbering backups).
Growth is a few hundred bytes per turn; rotate externally if it ever
matters. Failures to log never break a turn.
"""
import logging
import os
import threading
from datetime import datetime, timezone

_logger = None
_lock = threading.Lock()  # gthread workers: two first-callers must not both addHandler


def _get_logger():
    global _logger
    if _logger is not None:
        return _logger
    with _lock:
        if _logger is not None:
            return _logger
        log = logging.getLogger("agent.turns")
        log.setLevel(logging.INFO)
        log.propagate = False  # keep these lines out of the gunicorn stream
        if not log.handlers:
            try:
                from store.db import DB_PATH
                default_fp = os.path.join(os.path.dirname(DB_PATH), "agent_turns.log")
                fp = os.getenv("AGENT_TURN_LOG_FP", default_fp)
                # Opened eagerly: an unwritable path fails HERE, once, with a
                # warning — a lazy handler would instead fail inside every emit
                # and be swallowed silently by log_turn_event.
                handler = logging.FileHandler(fp, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(message)s"))
                log.addHandler(handler)
            except Exception as exc:
                logging.getLogger(__name__).warning("agent turn log disabled: %s", exc)
                log.addHandler(logging.NullHandler())
        _logger = log
        return log


def log_turn_event(chat_id, event, **fields):
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        detail = " ".join(f"{k}={str(v).replace(chr(13), ' ').replace(chr(10), ' ')[:200]}"
                          for k, v in fields.items())
        _get_logger().info(f"{ts} | chat={chat_id} | {event} | {detail}")
    except Exception:
        pass  # logging must never break a turn
