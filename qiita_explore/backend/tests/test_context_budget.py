"""Attached studies must all reach the model, and the header must not lie.

Reported from live use: two studies attached to a global chat, and the reply
said "the system notes that 2 studies were selected, but only Study 393's
details appear." The model was right. sel_budget was hardcoded
(min(14000, max(1500, 400*n))) and never looked at the budget the caller
computed, so on qwen3 — where context_budget_chars gives 3,507,000 chars — two
studies were allowed 1,500. A real compact block is ~774.

Broken for every count >= 2, and silent, because _format_discovery_study_list
built its header from len(studies) before packing.
"""
import sys
import types

import pytest


def _stub_qiita_core():
    if "qiita_core" in sys.modules:
        return
    fake_settings = types.ModuleType("qiita_core.qiita_settings")
    fake_settings.qiita_config = type("_FakeConfig", (), {})()
    fake_core = types.ModuleType("qiita_core")
    fake_core.qiita_settings = fake_settings
    sys.modules["qiita_core"] = fake_core
    sys.modules["qiita_core.qiita_settings"] = fake_settings


_stub_qiita_core()

from config import context_budget_chars  # noqa: E402
from helpers.llm_helpers import (  # noqa: E402
    _format_discovery_study_list,
    _study_discovery_compact_block,
    merge_global_chat_context,
)

# Real Qiita abstracts run to thousands of characters and _truncate caps them at
# 600, so a realistic block sits near its maximum. Short invented abstracts fit
# in the old budget and would hide the bug entirely — the first version of this
# reproduction did exactly that.
LONG_ABSTRACT = "A" * 3000


def study(i):
    return {
        "study_id": 1000 + i,
        "study_title": f"Study number {i} with a reasonably long descriptive title",
        "pi_name": "Some Principal Investigator",
        "pi_affiliation": "A University With A Long Name",
        "num_samples": 20, "num_preps": 1, "data_types": "16S",
        "study_abstract": LONG_ABSTRACT,
    }


def rendered(text):
    return text.count("- ID ")


class TestBlockSizeAssumption:
    """The 1_000-per-study rate is only correct if a block really fits in it."""

    def test_a_realistic_block_fits_the_per_study_allowance(self):
        size = len(_study_discovery_compact_block(study(1)))
        assert 700 < size <= 1_000, (
            f"a maxed-out compact block is {size} chars; the 1_000/study rate in "
            "merge_global_chat_context assumes it fits"
        )


class TestSelectedStudiesAllArrive:
    QWEN3 = None  # set in setup_method

    def setup_method(self):
        self.QWEN3 = context_budget_chars("qwen3")

    def test_the_reported_case_two_chips(self):
        """The exact bug: 2 attached, 1 delivered."""
        ctx = merge_global_chat_context([study(1), study(2)], [], "summarise these", self.QWEN3)
        assert rendered(ctx) == 2
        assert "1001" in ctx and "1002" in ctx

    @pytest.mark.parametrize("n", [1, 2, 3, 4, 10, 50])
    def test_every_chip_count_renders_in_full(self, n):
        ctx = merge_global_chat_context([study(i) for i in range(n)], [], "q", self.QWEN3)
        assert rendered(ctx) == n

    def test_no_truncation_notice_when_nothing_was_dropped(self):
        ctx = merge_global_chat_context([study(i) for i in range(10)], [], "q", self.QWEN3)
        assert "showing" not in ctx

    def test_scales_with_a_smaller_model_too(self):
        """gpt-oss has a 131k-token window; 20 chips must still fit."""
        ctx = merge_global_chat_context(
            [study(i) for i in range(20)], [], "q", context_budget_chars("gpt-oss")
        )
        assert rendered(ctx) == 20


class TestDatabaseResultsKeepRoom:
    """budget // 2 is the guard that replaced the old hardcoded ceiling."""

    def test_many_chips_do_not_starve_the_search_half(self):
        db = [study(500 + i) for i in range(8)]
        ctx = merge_global_chat_context(
            [study(i) for i in range(50)], db, "gut microbiome", context_budget_chars("qwen3")
        )
        assert "DATABASE SEARCH RESULTS" in ctx
        for s in db:
            assert str(s["study_id"]) in ctx, "search results must survive alongside 50 chips"

    def test_a_tiny_budget_still_leaves_both_sections(self):
        ctx = merge_global_chat_context([study(1), study(2)], [study(9)], "q", 8_000)
        assert "USER-SELECTED BROWSE CONTEXT" in ctx
        assert "DATABASE SEARCH RESULTS" in ctx


class TestHeaderTellsTheTruth:
    def test_truncation_is_stated_with_real_counts(self):
        out = _format_discovery_study_list(
            [study(i) for i in range(10)], "USER-SELECTED BROWSE CONTEXT (10 studies):", 2_500
        )
        n = rendered(out)
        assert 0 < n < 10
        assert f"showing {n} of 10" in out, (
            "the header must report what it rendered, not what it was asked for"
        )

    def test_header_count_always_matches_the_body(self):
        """The invariant that was violated. Sweep budgets across the boundary."""
        studies = [study(i) for i in range(6)]
        import re
        for budget in range(500, 6_000, 250):
            out = _format_discovery_study_list(studies, "SELECTED (6 studies):", budget)
            n = rendered(out)
            m = re.search(r"showing (\d+) of 6", out)
            if n < 6:
                assert m and int(m.group(1)) == n, f"budget {budget}: header disagrees with {n} blocks"
            else:
                assert m is None, f"budget {budget}: claimed truncation but rendered all 6"

    def test_no_studies_is_unchanged(self):
        assert "(none)" in _format_discovery_study_list([], "SELECTED:", 5_000)

    def test_a_budget_too_small_for_even_one_block_does_not_crash(self):
        out = _format_discovery_study_list([study(1)], "SELECTED (1 studies):", 50)
        assert rendered(out) == 0
        assert "showing 0 of 1" in out
