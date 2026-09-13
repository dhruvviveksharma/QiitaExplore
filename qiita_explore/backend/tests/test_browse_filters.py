"""Browse facet filters: request parsing, the SQL fragment, the Python
post-filter, the cached facet lists, and year narrowing on the deep-search
candidate query."""
from unittest.mock import patch

import pytest

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()

from services import browse_filters as bf  # noqa: E402
from services.browse_filters import (  # noqa: E402
    parse_browse_filters, build_browse_filter_where, study_passes_browse_filters,
    get_search_facets,
)


class TestParseBrowseFilters:
    def test_none_when_nothing_set(self):
        assert parse_browse_filters(None) is None
        assert parse_browse_filters({}) is None
        assert parse_browse_filters({"pis": [], "data_types": [], "year_min": None}) is None
        assert parse_browse_filters("junk") is None

    def test_normalises_lists_and_ints(self):
        f = parse_browse_filters({
            "pis": [" Rob Knight ", "Rob Knight", 5], "data_types": ["16S"],
            "year_min": "2012", "year_max": 2020,
        })
        assert f == {"pis": ["Rob Knight"], "data_types": ["16S"], "year_min": 2012, "year_max": 2020}

    def test_caps(self):
        assert len(parse_browse_filters({"pis": [f"p{i}" for i in range(80)]})["pis"]) == 50

    def test_swaps_inverted_years(self):
        f = parse_browse_filters({"year_min": 2020, "year_max": 2012})
        assert (f["year_min"], f["year_max"]) == (2012, 2020)

    def test_bad_year_raises(self):
        with pytest.raises(ValueError):
            parse_browse_filters({"year_min": "twenty"})
        with pytest.raises(ValueError):
            parse_browse_filters({"year_max": True})


class TestBuildBrowseFilterWhere:
    def test_empty(self):
        assert build_browse_filter_where() == ("", [])

    def test_pis_only(self):
        sql, params = build_browse_filter_where(pis=["Rob Knight"])
        assert sql == "sp_pi.name = ANY(%s)"
        assert params == [["Rob Knight"]]

    def test_year_range(self):
        sql, params = build_browse_filter_where(year_min=2012, year_max=2020)
        assert sql == ("EXTRACT(YEAR FROM s.first_contact) >= %s"
                       " AND EXTRACT(YEAR FROM s.first_contact) <= %s")
        assert params == [2012, 2020]

    def test_all_three_in_rendered_order(self):
        sql, params = build_browse_filter_where(["A", "B"], 2012, 2020)
        assert sql.count("%s") == 3 == len(params)
        assert params == [["A", "B"], 2012, 2020]
        assert sql.index("sp_pi.name") < sql.index(">= %s") < sql.index("<= %s")


class TestStudyPassesBrowseFilters:
    row = {"pi_name": "Rob Knight", "data_types": "16S, Metagenomic", "year": 2015}

    def test_no_filters_passes(self):
        assert study_passes_browse_filters(self.row) is True

    def test_pi(self):
        assert study_passes_browse_filters(self.row, pis=["Rob Knight"])
        assert not study_passes_browse_filters(self.row, pis=["Jeff Gordon"])
        assert not study_passes_browse_filters({"pi_name": None}, pis=["Rob Knight"])

    def test_data_type_any_overlap_on_comma_string(self):
        assert study_passes_browse_filters(self.row, data_types=["Metagenomic"])
        assert not study_passes_browse_filters(self.row, data_types=["ITS"])
        assert not study_passes_browse_filters({"data_types": None}, data_types=["16S"])

    def test_year_bounds_inclusive_and_missing_year_fails_only_when_bounded(self):
        assert study_passes_browse_filters(self.row, year_min=2015, year_max=2015)
        assert not study_passes_browse_filters(self.row, year_min=2016)
        assert not study_passes_browse_filters(self.row, year_max=2014)
        assert study_passes_browse_filters({"year": None})
        assert not study_passes_browse_filters({"year": None}, year_min=2000)


class TestGetSearchFacets:
    def setup_method(self):
        bf._facets_cache.update(at=0.0, data=None)

    @patch("services.browse_filters.pooled_fetchall")
    def test_shape_and_cache(self, mock_fetch):
        mock_fetch.side_effect = [
            [("Rob Knight", 312), ("Jeff Gordon", 40)],
            [(2011, 2026)],
            [("16S", 1450), ("Metagenomic", 600)],
        ]
        data = get_search_facets()
        assert data == {
            "pis": [{"name": "Rob Knight", "count": 312}, {"name": "Jeff Gordon", "count": 40}],
            "years": {"min": 2011, "max": 2026},
            "data_types": [{"name": "16S", "count": 1450}, {"name": "Metagenomic", "count": 600}],
        }
        assert mock_fetch.call_count == 3
        assert get_search_facets() is data          # served from cache
        assert mock_fetch.call_count == 3
        for call in mock_fetch.call_args_list:      # every facet is public-only
            assert "v.visibility = 'public'" in call.args[0]

    @patch("services.browse_filters.pooled_fetchall")
    def test_expired_cache_refetches(self, mock_fetch):
        mock_fetch.side_effect = [[], [(None, None)], []] * 2
        get_search_facets()
        bf._facets_cache["at"] = 0.0
        get_search_facets()
        assert mock_fetch.call_count == 6

    @patch("services.browse_filters.pooled_fetchall")
    def test_empty_db(self, mock_fetch):
        mock_fetch.side_effect = [[], [], []]
        assert get_search_facets()["years"] == {"min": None, "max": None}


class TestDeepSearchCandidateNarrowing:
    def test_params_bind_dt_then_pi_then_year_then_limit(self):
        import helpers.sample_search as ss
        calls = []

        class FakeTRN:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def add(self, sql, params): calls.append((sql, params))
            def execute_fetchindex(self): return [(1, 10), (2, 5)]

        with patch.object(ss, "TRN", FakeTRN()):
            ids = ss._get_candidate_ids(
                ["16S"], None, 500, resolved_pis=[{"name": "Knight"}],
                year_min=2012, year_max=2020,
            )
        assert ids == [1, 2]
        sql, params = calls[0]
        assert params == ["16S", ["Knight"], 2012, 2020, 1000]
        assert (sql.index("dt.data_type IN") < sql.index("unnest(%s::text[])")
                < sql.index("EXTRACT(YEAR") < sql.index("LIMIT %s"))

    def test_no_year_clause_without_bounds(self):
        import helpers.sample_search as ss
        calls = []

        class FakeTRN:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def add(self, sql, params): calls.append((sql, params))
            def execute_fetchindex(self): return []

        with patch.object(ss, "TRN", FakeTRN()):
            ss._get_candidate_ids(None, None, 40)
        sql, params = calls[0]
        assert "EXTRACT(YEAR" not in sql
        assert params == [80]
