"""The model must be able to READ what the UI displays.

Regression coverage for a bug where a project chat was asked "who are the PIs
in my studies", called get_study_report five times, and truthfully answered
that its context contained no PI — while every PI was on screen. The names went
into ui_payload.header (which the card renders) and never into ToolResult.text
(the only field the model receives).

The rule these tests encode: any study attribute the chat UI shows must also
appear in the model-visible text, or the model will contradict the screen.
"""
import sys
import types
from unittest.mock import patch

import pytest


def _stub_qiita_core():
    """helpers.qiita_fetch imports helpers.pg_pool, which imports qiita_core at
    module level — not installed in this sandbox. Same stub as test_auth.py:44
    and test_internal_tools_scope.py:51; the third copy, worth hoisting into
    conftest.py separately rather than inside this bug fix."""
    if "qiita_core" in sys.modules:
        return
    fake_settings = types.ModuleType("qiita_core.qiita_settings")
    fake_settings.qiita_config = type("_FakeConfig", (), {})()
    fake_core = types.ModuleType("qiita_core")
    fake_core.qiita_settings = fake_settings
    sys.modules["qiita_core"] = fake_core
    sys.modules["qiita_core.qiita_settings"] = fake_settings


_stub_qiita_core()

from helpers.llm_helpers import (  # noqa: E402  (must follow the stub)
    _format_pi_line,
    _build_workspace_manifest,
    _study_discovery_compact_block,
)


class TestFormatPiLine:
    def test_name_and_affiliation(self):
        assert _format_pi_line("Noah Fierer", "University of Colorado") == \
            "Noah Fierer (University of Colorado)"

    def test_name_only(self):
        assert _format_pi_line("Ruth Ley", None) == "Ruth Ley"
        assert _format_pi_line("Ruth Ley", "   ") == "Ruth Ley"

    def test_affiliation_only_does_not_print_na(self):
        """A study with an affiliation but no named PI should show the
        affiliation, not 'N/A (Stanford University)'."""
        assert _format_pi_line(None, "Stanford University") == "Stanford University"

    def test_missing_both_degrades_to_na_not_none(self):
        """Must not render the string 'None' into the model's context."""
        out = _format_pi_line(None, None)
        assert out == "N/A"
        assert "None" not in out


class TestWorkspaceManifest:
    """The manifest is what makes 'who are the PIs in my project' answerable
    with no tool call at all — the case that originally failed."""

    STUDIES = [
        {"study_id": 393, "study_title": "Forensic identification",
         "data_types": "16S", "pi_name": "Noah Fierer",
         "pi_affiliation": "University of Colorado"},
        {"study_id": 101, "study_title": "Infant gut succession",
         "data_types": "16S", "pi_name": "Ruth Ley",
         "pi_affiliation": "Cornell University"},
    ]

    def test_manifest_names_every_pi(self):
        out = _build_workspace_manifest({"studies": self.STUDIES})
        assert "Noah Fierer (University of Colorado)" in out
        assert "Ruth Ley (Cornell University)" in out

    def test_manifest_keeps_id_title_and_data_types(self):
        out = _build_workspace_manifest({"studies": self.STUDIES})
        assert "Study 393" in out and "Forensic identification" in out
        assert "16S" in out

    def test_study_with_no_pi_does_not_emit_none(self):
        out = _build_workspace_manifest(
            {"studies": [{"study_id": 7, "study_title": "T", "data_types": "16S"}]}
        )
        assert "PI: N/A" in out
        assert "None" not in out

    def test_empty_workspace_unchanged(self):
        assert "no studies added" in _build_workspace_manifest({"studies": []})


class TestSearchResultsUnaffected:
    """_study_discovery_compact_block was already correct; the refactor to a
    shared _format_pi_line must not change its output."""

    def test_search_block_still_shows_pi(self):
        out = _study_discovery_compact_block({
            "study_id": 803, "study_title": "Fermented milk strains",
            "pi_name": "Jeff Gordon", "pi_affiliation": "Washington University",
            "study_abstract": "abstract", "data_types": "16S", "num_samples": 179,
        })
        assert "PI: Jeff Gordon (Washington University)" in out

    def test_search_block_affiliation_only(self):
        out = _study_discovery_compact_block({
            "study_id": 1, "study_title": "T", "pi_affiliation": "UCSD",
        })
        assert "PI: UCSD |" in out


class TestFullSamplesBlock:
    """The string get_study_report actually hands the model."""

    HEADER = {
        "study_title": "Forensic identification using skin bacterial communities",
        "pi_name": "Noah Fierer",
        "pi_affiliation": "University of Colorado",
        "num_samples": 20,
        "data_types": "16S",
    }
    SAMPLES = [{"sample_id": "393.F2Mose396", "fields": {"env_package": "built environment"}}]

    def _build(self, header, samples=None):
        from helpers import qiita_fetch
        with patch.object(qiita_fetch, "_fetch_study_header_cached", return_value=header), \
             patch.object(qiita_fetch, "_get_or_fetch_full_samples",
                          return_value=self.SAMPLES if samples is None else samples):
            return qiita_fetch._build_full_samples_block(393, budget_chars=4_000)

    def test_report_text_names_the_pi(self):
        """The exact regression: this string had no PI, so the model denied
        having one while the card above it displayed 'Noah Fierer'."""
        assert "PI: Noah Fierer (University of Colorado)" in self._build(self.HEADER)

    def test_pi_survives_a_tight_budget(self):
        """PI is header, not a sample row — it must not be the thing that gets
        truncated away when the budget is small."""
        from helpers import qiita_fetch
        many = [{"sample_id": f"s{i}", "fields": {"k": "v" * 50}} for i in range(200)]
        with patch.object(qiita_fetch, "_fetch_study_header_cached", return_value=self.HEADER), \
             patch.object(qiita_fetch, "_get_or_fetch_full_samples", return_value=many):
            out = qiita_fetch._build_full_samples_block(393, budget_chars=500)
        assert "PI: Noah Fierer (University of Colorado)" in out
        assert "truncated" in out

    def test_missing_pi_degrades_cleanly(self):
        out = self._build({"study_title": "T", "num_samples": 1, "data_types": "16S"})
        assert "PI: N/A" in out
        assert "None" not in out

    def test_study_with_no_samples_still_reports_pi(self):
        out = self._build(self.HEADER, samples=[])
        assert "PI: Noah Fierer (University of Colorado)" in out
        assert "No sample metadata available" in out
