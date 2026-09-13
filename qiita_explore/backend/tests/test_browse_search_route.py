"""Route-level tests for POST /api/search (the Browse box) — the SQL and
sample-metadata layers are mocked, so these pin what the route hands them:
the planner's WHERE, the exact-ID boost, and when the deep fan-out runs.
"""
import os
import sys
from unittest.mock import patch

import pytest

from .conftest import stub_qiita_db_and_core


@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    """Same idiom as test_auth._app: import run once per module, bound to an
    isolated SQLite file, after purging any earlier instance."""
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = str(tmp_path_factory.mktemp("search_route") / "test.db")
    for name in list(sys.modules):
        if (name == "run" or name.startswith("routes.") or name == "store" or name.startswith("store.")
                or name.startswith("helpers.") or "sql_store" in name):
            del sys.modules[name]
    stub_qiita_db_and_core()
    import run
    return run.app


def _header(sid, **extra):
    return {
        "study_id": sid, "study_title": f"Study {sid}", "study_abstract": "", "study_alias": "",
        "metadata_complete": True, "pi_name": "PI", "pi_email": None, "pi_affiliation": "X",
        "lab_person_name": None, "num_samples": 10, "data_types": "16S", "num_preps": 1,
        "is_gold": False, **extra,
    }


def _call(app, body, sql_rows=None, meta_rows=None):
    """Invoke the view directly inside a request context (bypasses the
    session before_request) with the three heavy collaborators mocked; return
    (status, json, mocks)."""
    import routes.study_routes as sr
    with patch.object(sr, "search_studies_with_sql", return_value=list(sql_rows or [])) as m_sql, \
         patch.object(sr, "search_studies_by_sample_meta", return_value=list(meta_rows or [])) as m_meta, \
         patch.object(sr, "finalize_search_results", side_effect=lambda studies, *a, **k: studies) as m_fin:
        with app.test_request_context("/api/search", method="POST", json=body):
            rv = sr.search()
    resp, status = (rv if isinstance(rv, tuple) else (rv, 200))
    return status, resp.get_json(), {"sql": m_sql, "meta": m_meta, "fin": m_fin}


class TestExactIdQueries:
    def test_pure_id_query_is_exact_match_only_and_skips_deep_search(self, _app):
        status, body, m = _call(_app, {"query": "study id 550"}, sql_rows=[_header(550)])
        assert status == 200
        kw = m["sql"].call_args.kwargs
        assert kw["custom_sql_where"] == "s.study_id = ANY(%s)"
        assert kw["params"] == [[550]]
        assert kw["relevance_keywords"] is None
        assert kw["boost_study_ids"] is None
        assert not m["meta"].called
        assert not m["fin"].called
        assert [s["study_id"] for s in body["results"]] == [550]
        assert body["sql_query"]["id_only"] is True

    def test_mixed_query_boosts_the_id_and_still_deep_searches(self, _app):
        status, body, m = _call(_app, {"query": "550 mouse gut"}, sql_rows=[_header(550), _header(7)])
        assert status == 200
        kw = m["sql"].call_args.kwargs
        assert kw["boost_study_ids"] == [550]
        assert "mouse" in kw["relevance_keywords"]
        assert "550" not in kw["relevance_keywords"]
        assert kw["title_phrase"] == "mouse gut"
        assert m["meta"].called
        assert m["fin"].call_args.kwargs["boost_study_ids"] == [550]
        assert m["fin"].call_args.kwargs["title_phrase"] == "mouse gut"

    def test_empty_query_is_rejected(self, _app):
        status, body, _ = _call(_app, {"query": ""})
        assert status == 400


class TestBrowseFilters:
    def test_filter_only_query_binds_in_topic_slot_and_skips_deep_search(self, _app):
        body = {"query": "", "filters": {
            "pis": ["Rob Knight"], "data_types": ["16S"], "year_min": 2012, "year_max": 2020,
        }}
        status, resp, m = _call(_app, body, sql_rows=[_header(1, pi_name="Rob Knight", year=2015)])
        assert status == 200
        kw = m["sql"].call_args.kwargs
        assert kw["custom_sql_where"] == (
            "(1=1) AND sp_pi.name = ANY(%s)"
            " AND EXTRACT(YEAR FROM s.first_contact) >= %s"
            " AND EXTRACT(YEAR FROM s.first_contact) <= %s"
        )
        assert kw["params"] == [["Rob Knight"], 2012, 2020]
        assert kw["data_types"] == ["16S"]
        assert kw["limit"] == 120
        assert kw["relevance_keywords"] is None
        assert not m["meta"].called            # no terms to probe with
        assert resp["filters"] == {"pis": ["Rob Knight"], "data_types": ["16S"],
                                   "year_min": 2012, "year_max": 2020}
        assert resp["results"][0]["year"] == 2015

    def test_deep_search_rows_are_post_filtered_and_candidates_narrowed(self, _app):
        body = {"query": "gut", "filters": {"year_min": 2012}}
        status, resp, m = _call(
            _app, body, sql_rows=[_header(1, year=2015)],
            meta_rows=[_header(2, year=2009), _header(3, year=2018)],
        )
        assert status == 200
        assert [s["study_id"] for s in resp["results"]] == [1, 3]
        mk = m["meta"].call_args.kwargs
        assert mk["year_min"] == 2012 and mk["year_max"] is None
        assert mk["data_types"] is None

    def test_facet_pis_narrow_deep_candidates_and_gate_exactly(self, _app):
        body = {"query": "gut", "filters": {"pis": ["Rob Knight"]}}
        _, resp, m = _call(
            _app, body, sql_rows=[_header(1, pi_name="Rob Knight")],
            meta_rows=[_header(2, pi_name="Someone Else")],
        )
        assert [s["study_id"] for s in resp["results"]] == [1]
        assert m["meta"].call_args.kwargs["resolved_pis"] == [{"name": "Rob Knight"}]

    def test_bad_year_is_400(self, _app):
        status, _, _ = _call(_app, {"query": "", "filters": {"year_min": "twenty"}})
        assert status == 400

    def test_query_without_filters_echoes_null_filters(self, _app):
        _, resp, m = _call(_app, {"query": "gut"}, sql_rows=[_header(1)])
        assert resp["filters"] is None
        assert m["sql"].call_args.kwargs["data_types"] is None


class TestFacetsEndpoint:
    def test_returns_facets(self, _app):
        import routes.study_routes as sr
        facets = {"pis": [], "years": {"min": 2011, "max": 2026}, "data_types": []}
        with patch.object(sr, "get_search_facets", return_value=facets):
            with _app.test_request_context("/api/search/facets"):
                rv = sr.api_search_facets()
        assert rv.get_json() == facets
