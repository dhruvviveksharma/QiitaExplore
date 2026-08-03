"""Pinned-study context budgeting, and the stub fallback in the discovery formatter.

Both subsystems used to discard studies past a hardcoded budget with no trace,
so the model could neither see them nor reach them with get_study_report. These
assert the budget arithmetic and that nothing is dropped silently.
"""
import pytest


@pytest.fixture
def pinned(fresh_db, monkeypatch):
    from .conftest import stub_qiita_db_and_core
    stub_qiita_db_and_core()
    import helpers.pinned_context as pc
    return pc


def _samples(n, chars_per_sample=900):
    """n sample dicts that each render to roughly chars_per_sample."""
    pad = "x" * max(1, chars_per_sample - 40)
    return [{"sample_id": f"s{i}", "fields": {"description": pad}} for i in range(n)]


def _header(num_samples, title="A Study", data_types="16S"):
    return {"study_title": title, "num_samples": num_samples, "data_types": data_types}


# ── budget arithmetic ────────────────────────────────────────────────────────

class TestPerStudyBudget:
    def test_flat_constant_on_a_large_model(self, pinned):
        # gemma: 889,504 chars * 0.65 / 5 = 115,635 -> the 60k constant wins
        assert pinned._pinned_per_study_budget(5, "gemma") == 60_000

    def test_clamp_fires_on_a_131k_model(self, pinned):
        # gpt-oss: 430,752 * 0.65 // 5 = 55,997, just under the constant.
        # This is the assertion that stops 5 x 60k overflowing a 131k window.
        assert pinned._pinned_per_study_budget(5, "gpt-oss") == 55_997

    def test_assembled_worst_case_fits_the_smallest_model(self, pinned):
        from config import context_budget_chars
        per = pinned._pinned_per_study_budget(5, "gpt-oss")
        assert per * 5 < context_budget_chars("gpt-oss")

    def test_single_pin_gets_the_full_constant_everywhere(self, pinned):
        for model in ("gpt-oss", "gemma", "qwen3"):
            assert pinned._pinned_per_study_budget(1, model) == 60_000


# ── inline vs manifest ───────────────────────────────────────────────────────

class TestInlineAndManifest:
    def test_seven_pins_inline_five_and_manifest_two(self, pinned, monkeypatch):
        monkeypatch.setattr(pinned, "_build_full_samples_block",
                            lambda sid, budget_chars: f"### Study {sid}: inlined")
        monkeypatch.setattr(pinned, "_fetch_study_header_cached",
                            lambda sid: _header(42, title=f"Study {sid} title"))

        text = pinned._build_pinned_reports_context([1, 2, 3, 4, 5, 6, 7], "gemma")

        for sid in (1, 2, 3, 4, 5):
            assert f"### Study {sid}: inlined" in text
        assert "ALSO PINNED (2 more" in text
        # The manifest must carry the ID and the tool name, or the model cannot
        # reach the studies it just learned about.
        for sid in (6, 7):
            assert f"- ID {sid}:" in text
            assert f"get_study_report({sid})" in text

    def test_no_manifest_section_when_everything_is_inlined(self, pinned, monkeypatch):
        monkeypatch.setattr(pinned, "_build_full_samples_block",
                            lambda sid, budget_chars: f"### Study {sid}")
        text = pinned._build_pinned_reports_context([1, 2], "gemma")
        assert "ALSO PINNED" not in text

    def test_empty_returns_none(self, pinned):
        assert pinned._build_pinned_reports_context([], "gemma") is None


# ── per-study block ──────────────────────────────────────────────────────────

class TestFullSamplesBlock:
    def test_cheap_study_spends_only_what_it_needs(self, pinned, monkeypatch):
        monkeypatch.setattr(pinned, "_fetch_study_header_cached", lambda sid: _header(5))
        monkeypatch.setattr(pinned, "_get_or_fetch_full_samples",
                            lambda sid, limit=None: _samples(5))
        block = pinned._build_full_samples_block(101, 60_000)
        assert len(block) < 10_000, "a 5-sample study must not pad out to the budget"
        assert "showed" not in block, "nothing was truncated, so no notice belongs here"

    def test_truncation_notice_names_the_escape_hatch(self, pinned, monkeypatch):
        monkeypatch.setattr(pinned, "_fetch_study_header_cached", lambda sid: _header(4736))
        monkeypatch.setattr(pinned, "_get_or_fetch_full_samples",
                            lambda sid, limit=None: _samples(400))
        block = pinned._build_full_samples_block(13722, 20_000)
        assert "4736" in block, "the notice must state the true total, not the fetched count"
        assert "get_study_report(13722)" in block

    def test_fetch_limit_scales_with_the_budget(self, pinned, monkeypatch):
        """The old code fetched a hardcoded 200 rows, so the char budget could
        never be spent on a large study."""
        seen = {}
        monkeypatch.setattr(pinned, "_fetch_study_header_cached", lambda sid: _header(5000))
        def _capture(sid, limit=None):
            seen["limit"] = limit
            return _samples(10)
        monkeypatch.setattr(pinned, "_get_or_fetch_full_samples", _capture)
        pinned._build_full_samples_block(1, 60_000)
        assert seen["limit"] == 500      # 60_000 // _MIN_CHARS_PER_SAMPLE
        pinned._build_full_samples_block(1, 6_000)
        assert seen["limit"] == 200      # floored at REPORT_SAMPLE_LIMIT


# ── discovery formatter stubs ────────────────────────────────────────────────

class TestDiscoveryStubs:
    @pytest.fixture
    def fmt(self, fresh_db):
        from .conftest import stub_qiita_db_and_core
        stub_qiita_db_and_core()
        from helpers.llm_helpers import _format_discovery_study_list
        return _format_discovery_study_list

    def _studies(self, n):
        return [{"study_id": 100 + i, "study_title": f"Study {100 + i}",
                 "study_abstract": "a" * 600, "pi_name": "Dr. Test",
                 "num_samples": 10, "data_types": "16S"} for i in range(n)]

    def test_everything_fits_under_a_generous_budget(self, fmt):
        out = fmt(self._studies(8), "HEADER (8 studies):", 100_000)
        for i in range(8):
            assert f"ID {100 + i}:" in out
        assert "not shown in full" not in out

    def test_overflow_becomes_stubs_not_silence(self, fmt):
        """This is the regression: studies past the budget used to vanish with
        no ID, so the model could neither see nor fetch them.

        Asserted as an invariant rather than a fixed split — every study must be
        reachable, whether inlined or stubbed, however the budget happens to
        divide."""
        out = fmt(self._studies(5), "HEADER (5 studies):", 1_500)
        for i in range(5):
            assert f"ID {100 + i}:" in out, f"study {100 + i} vanished entirely"
        assert "not shown in full" in out, "some studies were omitted, so say so"
        assert "get_study_report" in out
        # The stated count must match the stubs actually listed. Count only in
        # the tail after the marker — full blocks also open with "- ID ".
        import re
        head, _, tail = out.partition("not shown in full")
        stated = int(re.search(r"\((\d+) more $", head).group(1))
        assert stated == sum(1 for ln in tail.splitlines() if ln.startswith("- ID "))

    def test_order_is_preserved_across_the_cut(self, fmt):
        """break, not continue — a later smaller study must not leapfrog an
        earlier one that didn't fit."""
        studies = self._studies(3)
        studies[1]["study_abstract"] = "b" * 600
        studies[2]["study_abstract"] = "c"           # much smaller
        out = fmt(studies, "HEADER:", 1_500)
        assert "- ID 102:" in out, "the small last study must stay in the stub tail"
