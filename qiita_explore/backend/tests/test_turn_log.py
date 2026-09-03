"""Smoke tests for helpers/turn_log.py — the per-turn lifecycle log that
answers "when and where did the LLM stop responding?"."""
import importlib
import logging

import pytest

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()


def _clear_singleton_handlers():
    """agent.turns is a process-global named logger — a stale handler from
    an earlier test (or an earlier call in this same test) survives even
    after the module-level `_logger` cache is reset, unless cleared here."""
    log = logging.getLogger("agent.turns")
    for h in list(log.handlers):
        h.close()
        log.removeHandler(h)


@pytest.fixture
def turn_log(monkeypatch, tmp_path):
    """Fresh turn_log module writing to a temp file; resets the cached logger
    (module global AND the singleton's handlers) so neither this test nor
    later ones inherit the wrong handler."""
    import helpers.turn_log as tl
    monkeypatch.setenv("AGENT_TURN_LOG_FP", str(tmp_path / "agent_turns.log"))
    _clear_singleton_handlers()
    tl._logger = None
    yield tl, tmp_path / "agent_turns.log"
    _clear_singleton_handlers()
    tl._logger = None


def test_events_land_as_greppable_lines(turn_log):
    tl, fp = turn_log
    tl.log_turn_event("chat-abc", "turn_start", scope="global", model="minimax-m2")
    tl.log_turn_event("chat-abc", "tool_fail", name="search_studies",
                      detail='malformed array literal: "Metagenomic"')
    tl.log_turn_event("chat-abc", "turn_done", chars=1234, segments=3)

    lines = fp.read_text().strip().splitlines()
    assert len(lines) == 3
    assert "| chat=chat-abc | turn_start | scope=global model=minimax-m2" in lines[0]
    assert "tool_fail" in lines[1] and "malformed array literal" in lines[1]
    assert "turn_done" in lines[2] and "chars=1234" in lines[2]


def test_field_values_truncate_at_200_chars(turn_log):
    tl, fp = turn_log
    tl.log_turn_event("c1", "tool_fail", detail="x" * 500)
    line = fp.read_text().strip()
    assert "x" * 200 in line
    assert "x" * 201 not in line


def test_logging_never_raises(turn_log):
    tl, fp = turn_log
    # Unserializable-ish values and a broken logger must both be swallowed.
    tl.log_turn_event(None, "weird", obj=object())
    tl._logger = None
    importlib.reload(tl)  # re-imported module still logs without raising
    tl.log_turn_event("c2", "after_reload")

    # The named "agent.turns" logger is process-global; resetting only the
    # module-level cache must not stack a second handler onto it.
    lines = [line for line in fp.read_text().strip().splitlines() if "after_reload" in line]
    assert len(lines) == 1


def test_multiline_values_stay_on_one_line(turn_log):
    tl, fp = turn_log
    tl.log_turn_event("c1", "tool_fail",
                      detail='bad\r\nLINE 2: ...\n^')
    lines = fp.read_text().strip().splitlines()
    assert len(lines) == 1
    assert "LINE 2:" in lines[0]


def test_unwritable_path_warns_once_and_degrades_to_noop(turn_log, monkeypatch, tmp_path, caplog):
    # The handler is opened eagerly so a bad path fails HERE, once, with a
    # warning — a lazily-opened handler would fail inside every emit and be
    # swallowed silently by log_turn_event, disabling the log with no trace.
    tl, _ = turn_log
    monkeypatch.setenv("AGENT_TURN_LOG_FP", str(tmp_path / "no-such-dir" / "agent_turns.log"))
    with caplog.at_level(logging.WARNING, logger="helpers.turn_log"):
        tl.log_turn_event("c1", "turn_start")
        tl.log_turn_event("c1", "turn_done")  # cached — must not warn again
    warnings = [r for r in caplog.records if "agent turn log disabled" in r.getMessage()]
    assert len(warnings) == 1
