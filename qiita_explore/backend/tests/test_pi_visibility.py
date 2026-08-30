"""Model-visible study text must include every attribute the UI card shows —
found live as: project chat asked "who are the PIs", the model truthfully said
its context had no PI info, while every PI was rendered on screen.
"""
import pytest

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()

from helpers.llm_helpers import _format_pi_line, _study_discovery_compact_block  # noqa: E402


@pytest.fixture
def pinned(fresh_db):
    import helpers.pinned_context as pc
    return pc


def _header(pi_name="Noah Fierer", pi_affiliation="University of Colorado",
            num_samples=42, title="Soil Study"):
    return {"study_title": title, "num_samples": num_samples, "data_types": "16S",
            "pi_name": pi_name, "pi_affiliation": pi_affiliation}


def _samples(n, chars_per_sample=200):
    pad = "x" * max(1, chars_per_sample - 40)
    return [{"sample_id": f"s{i}", "fields": {"description": pad}} for i in range(n)]


class TestFormatPiLine:

    def test_name_and_affiliation(self):
        assert _format_pi_line("Rob Knight", "UCSD") == "Rob Knight (UCSD)"

    def test_name_only(self):
        assert _format_pi_line("Rob Knight", None) == "Rob Knight"

    def test_affiliation_only(self):
        assert _format_pi_line(None, "UCSD") == "UCSD"

    def test_missing_both_degrades_to_na_not_none(self):
        assert _format_pi_line(None, "") == "N/A"


class TestReportBlockPiVisibility:

    def test_report_text_names_the_pi(self, pinned, monkeypatch):
        monkeypatch.setattr(pinned, "_fetch_study_header_cached", lambda sid: _header())
        monkeypatch.setattr(pinned, "_get_or_fetch_full_samples", lambda sid, limit=None: _samples(3))
        block = pinned._build_full_samples_block(393, 10_000)
        assert "PI: Noah Fierer (University of Colorado)" in block

    def test_pi_survives_a_tight_budget(self, pinned, monkeypatch):
        monkeypatch.setattr(pinned, "_fetch_study_header_cached", lambda sid: _header())
        monkeypatch.setattr(pinned, "_get_or_fetch_full_samples", lambda sid, limit=None: _samples(200))
        block = pinned._build_full_samples_block(393, 500)
        assert "PI: Noah Fierer" in block

    def test_missing_pi_degrades_cleanly(self, pinned, monkeypatch):
        monkeypatch.setattr(pinned, "_fetch_study_header_cached",
                            lambda sid: _header(pi_name=None, pi_affiliation=None))
        monkeypatch.setattr(pinned, "_get_or_fetch_full_samples", lambda sid, limit=None: _samples(2))
        block = pinned._build_full_samples_block(393, 10_000)
        assert "PI: N/A" in block
        assert "None" not in block

    def test_study_with_no_samples_still_reports_pi(self, pinned, monkeypatch):
        monkeypatch.setattr(pinned, "_fetch_study_header_cached", lambda sid: _header())
        monkeypatch.setattr(pinned, "_get_or_fetch_full_samples", lambda sid, limit=None: [])
        block = pinned._build_full_samples_block(393, 10_000)
        assert "PI: Noah Fierer (University of Colorado)" in block
        assert "No sample metadata available" in block


class TestSearchBlockUnaffected:
    """The _format_pi_line extraction must be behavior-preserving for the
    discovery block that already rendered PI."""

    def test_search_block_still_shows_pi(self):
        block = _study_discovery_compact_block({
            "study_id": 1, "study_title": "T", "pi_name": "Jane Doe",
            "pi_affiliation": "MIT", "study_abstract": "a", "data_types": "16S",
            "num_samples": 5, "num_preps": 1,
        })
        assert "Jane Doe (MIT)" in block

    def test_search_block_affiliation_only(self):
        block = _study_discovery_compact_block({
            "study_id": 1, "study_title": "T", "pi_name": None,
            "pi_affiliation": "MIT", "study_abstract": "a", "data_types": "16S",
            "num_samples": 5, "num_preps": 1,
        })
        assert "MIT" in block

    def test_search_block_missing_both_shows_na(self):
        block = _study_discovery_compact_block({
            "study_id": 1, "study_title": "T", "pi_name": None,
            "pi_affiliation": None, "study_abstract": "a", "data_types": "16S",
            "num_samples": 5, "num_preps": 1,
        })
        assert "N/A" in block
