"""Smoke tests for helpers/turn_log.py — the per-turn lifecycle log that
answers "when and where did the LLM stop responding?"."""
import importlib

import pytest

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()


@pytest.fixture
def turn_log(monkeypatch, tmp_path):
    """Fresh turn_log module writing to a temp file; resets the cached logger
    so neither this test nor later ones inherit the wrong handler."""
    import helpers.turn_log as tl
    monkeypatch.setenv("AGENT_TURN_LOG_FP", str(tmp_path / "agent_turns.log"))
    tl._logger = None
    yield tl, tmp_path / "agent_turns.log"
    if tl._logger is not None:
        for h in list(tl._logger.handlers):
            h.close()
            tl._logger.removeHandler(h)
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
    tl, _ = turn_log
    # Unserializable-ish values and a broken logger must both be swallowed.
    tl.log_turn_event(None, "weird", obj=object())
    tl._logger = None
    importlib.reload(tl)  # re-imported module still logs without raising
    tl.log_turn_event("c2", "after_reload")
