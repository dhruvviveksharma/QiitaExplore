"""Forward specs for the Phase 4 compaction module (helpers/chat_history.py).

Skipped until build_agent_history's compaction path exists.
"""
import pytest

pytestmark = pytest.mark.skip(reason="pending Phase 4: helpers/chat_history.py compaction")


def test_compaction_triggers_above_budget_and_persists_anchor():
    """Tiny env-override budget forces compaction: older turns are summarized
    via one llm_chat call, compacted_through_id lands on an assistant row id,
    and step_start/step_done compaction events are yielded."""


def test_recompaction_reanchors_instead_of_resummarizing():
    """A second compaction summarizes only turns after the previous anchor."""
