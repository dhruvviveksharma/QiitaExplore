"""Pinned-study context budgeting, and the stub fallback in the discovery formatter.

Both subsystems used to discard studies past a hardcoded budget with no trace,
so the model could neither see them nor reach them with get_study_report. These
assert the budget arithmetic and that nothing is dropped silently.
"""
import pytest


@pytest.fixture
def pinned(fresh_db):
    import helpers.pinned_context as pc
    return pc


@pytest.fixture
def qfetch(fresh_db):
    import helpers.qiita_fetch as qf
    return qf


def _samples(n, chars_per_sample=900):
    """n sample dicts that each render to roughly chars_per_sample."""
    pad = "x" * max(1, chars_per_sample - 40)
    return [{"sample_id": f"s{i}", "fields": {"description": pad}} for i in range(n)]


def _header(num_samples, title="A Study", data_types="16S"):
    return {"study_title": title, "num_samples": num_samples, "data_types": data_types}


# ── budget arithmetic ────────────────────────────────────────────────────────

class TestPerStudyBudget:
    def test_flat_constant_on_a_large_model(self, pinned):
        # deepseek-v4-flash: 3,642,016 chars * 0.65 / 5 = 473,462 -> the 60k constant wins
        assert pinned._pinned_per_study_budget(5, "deepseek-v4-flash") == 60_000

    def test_clamp_fires_on_a_131k_model(self, pinned, monkeypatch):
        # A synthetic small-context model (131,072 tokens, like the smallest
        # models used to be) rather than a real registry entry — this test
        # is about the clamp math, not about which models happen to be
        # configured today. 430,752 * 0.65 // 5 = 55,997, just under the
        # constant. This is the assertion that stops 5 x 60k overflowing a
        # 131k window.
        import config
        monkeypatch.setitem(config.MODEL_METADATA, "test-small-model", {"context": 131_072})
        assert pinned._pinned_per_study_budget(5, "test-small-model") == 55_997

    def test_assembled_worst_case_fits_the_smallest_model(self, pinned, monkeypatch):
        import config
        from config import context_budget_chars
        monkeypatch.setitem(config.MODEL_METADATA, "test-small-model", {"context": 131_072})
        per = pinned._pinned_per_study_budget(5, "test-small-model")
        assert per * 5 < context_budget_chars("test-small-model")

    def test_single_pin_gets_the_full_constant_everywhere(self, pinned):
        for model in ("deepseek-v4-flash", "glm-5", "minimax-m2"):
            assert pinned._pinned_per_study_budget(1, model) == 60_000


# ── inline vs manifest ───────────────────────────────────────────────────────

class TestInlineAndManifest:
    def test_seven_pins_inline_five_and_manifest_two(self, pinned, monkeypatch):
        monkeypatch.setattr(pinned, "_build_full_samples_block",
                            lambda sid, budget_chars, **kw: f"### Study {sid}: inlined")
        monkeypatch.setattr(pinned, "_fetch_study_header_cached",
                            lambda sid: _header(42, title=f"Study {sid} title"))

        text = pinned._build_pinned_reports_context([1, 2, 3, 4, 5, 6, 7], "deepseek-v4-flash")

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
                            lambda sid, budget_chars, **kw: f"### Study {sid}")
        text = pinned._build_pinned_reports_context([1, 2], "deepseek-v4-flash")
        assert "ALSO PINNED" not in text

    def test_empty_returns_none(self, pinned):
        assert pinned._build_pinned_reports_context([], "deepseek-v4-flash") is None

    def test_without_tools_every_study_is_inlined(self, pinned, monkeypatch):
        """Project chat and /pin stream through llm_chat_stream with no tools, so
        a manifest line telling the model to call get_study_report is an
        instruction it cannot follow. All 7 must be inlined instead."""
        monkeypatch.setattr(pinned, "_build_full_samples_block",
                            lambda sid, budget_chars, **kw: f"### Study {sid}: inlined")
        text = pinned._build_pinned_reports_context(
            [1, 2, 3, 4, 5, 6, 7], "deepseek-v4-flash", tools_available=False)

        for sid in range(1, 8):
            assert f"### Study {sid}: inlined" in text
        assert "ALSO PINNED" not in text
        assert "get_study_report" not in text

    def test_without_tools_the_budget_is_split_across_all_studies(self, pinned, monkeypatch):
        seen = []
        monkeypatch.setattr(pinned, "_build_full_samples_block",
                            lambda sid, budget_chars, **kw: seen.append(budget_chars) or "x")
        pinned._build_pinned_reports_context([1, 2, 3, 4, 5, 6, 7], "deepseek-v4-flash",
                                             tools_available=False)
        # 7 studies share the window, not 5 — otherwise 7 x 60k would be handed out.
        assert len(seen) == 7
        assert all(b == pinned._pinned_per_study_budget(7, "deepseek-v4-flash") for b in seen)

    def test_truncation_notice_omits_the_hatch_without_tools(self, pinned, monkeypatch):
        monkeypatch.setattr(pinned, "_fetch_study_header_cached", lambda sid: _header(4736))
        monkeypatch.setattr(pinned, "_get_or_fetch_full_samples",
                            lambda sid, limit=None: _samples(400))
        block = pinned._build_full_samples_block(13722, 20_000, tools_available=False)
        assert "4736" in block, "the true total must still be stated"
        assert "get_study_report" not in block


# ── sample cache ─────────────────────────────────────────────────────────────

class TestFullSamplesCache:
    """The pinned path calls _get_or_fetch_full_samples 5x per turn, so a cache
    that can't be reused is 5 wasted Postgres round-trips on every message.

    The cache accessors are faked with a dict rather than exercised through
    SQLite: `fresh_db` purges `store.*` but not `helpers.*`, so qiita_fetch keeps
    its accessors bound to a stale store module. The sufficiency rule is the
    thing under test here, and this pins it exactly.
    """

    @pytest.fixture
    def cached(self, qfetch, monkeypatch):
        store = {}
        monkeypatch.setattr(qfetch, "get_study_detail_cache", lambda sid: store.get(int(sid)))
        def _upsert(sid, *a, full_samples_json=None, full_samples_limit=None, **kw):
            store[int(sid)] = {"full_samples_json": full_samples_json,
                               "full_samples_limit": full_samples_limit}
        monkeypatch.setattr(qfetch, "upsert_study_detail_cache", _upsert)
        return store

    def test_small_study_is_served_from_cache_on_the_second_call(self, qfetch, cached, monkeypatch):
        """The regression: sufficiency compared len(samples) against num_samples,
        which counts the qiita_sample_column_names sentinel that this query
        excludes. total was always larger, so any study below the limit
        re-fetched from Postgres forever."""
        calls = []
        monkeypatch.setattr(qfetch, "_fetch_full_sample_metadata",
                            lambda study_id, limit: calls.append(limit) or _samples(63))

        first  = qfetch._get_or_fetch_full_samples(101, limit=500)
        second = qfetch._get_or_fetch_full_samples(101, limit=500)

        assert len(first) == 63 and len(second) == 63
        assert calls == [500], f"expected one Postgres fetch, got {len(calls)}"

    def test_a_larger_request_refetches_once_then_sticks(self, qfetch, cached, monkeypatch):
        calls = []
        monkeypatch.setattr(qfetch, "_fetch_full_sample_metadata",
                            lambda study_id, limit: calls.append(limit) or _samples(limit))

        qfetch._get_or_fetch_full_samples(101, limit=200)
        qfetch._get_or_fetch_full_samples(101, limit=500)   # cache holds only 200
        qfetch._get_or_fetch_full_samples(101, limit=500)   # now satisfied
        assert calls == [200, 500]

    def test_a_smaller_request_is_served_from_a_bigger_cache(self, qfetch, cached, monkeypatch):
        calls = []
        monkeypatch.setattr(qfetch, "_fetch_full_sample_metadata",
                            lambda study_id, limit: calls.append(limit) or _samples(limit))

        qfetch._get_or_fetch_full_samples(101, limit=500)
        rows = qfetch._get_or_fetch_full_samples(101, limit=200)
        assert calls == [500]
        assert len(rows) == 200, "a smaller request must be trimmed from the cache"


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

    def test_without_tools_the_stubs_stay_but_the_hatch_goes(self, fmt):
        """When tools_available=False, omitted studies still list their IDs but
        must not instruct the model to call a report tool it cannot invoke."""
        out = fmt(self._studies(5), "HEADER (5 studies):", 1_500, tools_available=False)
        for i in range(5):
            assert f"ID {100 + i}:" in out
        assert "not shown in full" in out
        assert "get_study_report" not in out
