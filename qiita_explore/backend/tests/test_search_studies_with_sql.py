"""Unit tests for search_studies_with_sql's SQL/param assembly — mocks
pooled_fetchall so these run without a live Postgres connection, verifying
the actual query text and param list psycopg2 would receive.
"""
import re
from unittest.mock import patch

from tests.conftest import stub_qiita_db_and_core

stub_qiita_db_and_core()

from services.study_service import search_studies_with_sql


def _capture_call(**kwargs):
    """Run search_studies_with_sql with pooled_fetchall mocked out; return
    the (sql, params) it was called with."""
    with patch("services.study_service.pooled_fetchall", return_value=[]) as mock_fetch:
        search_studies_with_sql(**kwargs)
        assert mock_fetch.called
        sql, params = mock_fetch.call_args[0]
        return sql, params


class TestTagFilterParamPosition:
    """tags is threaded through with the SAME relative-ordering convention
    data_types already used (dt_params, then tag_params, then the custom
    WHERE's own params) — see the docstring note in search_studies_with_sql
    about this convention not matching the WHERE text's literal left-to-right
    placeholder order. This test locks in that tag_params lands immediately
    after dt_params, matching tag_sql's position immediately after dt_sql in
    the WHERE text (unambiguous regardless of the custom/dt question)."""

    def test_tag_clause_present_when_tags_given(self):
        sql, params = _capture_call(tags=["GOLD"])
        assert "qiita.per_study_tags" in sql
        assert "GOLD" in params

    def test_no_tag_clause_when_tags_omitted(self):
        # qiita.per_study_tags always appears once, for the unrelated
        # always-on is_gold display column — the filter clause specifically
        # (study_tag IN (...)) must be absent when no tags are requested.
        sql, params = _capture_call()
        assert "study_tag IN (" not in sql

    def test_tag_params_land_between_dt_and_custom_params(self):
        sql, params = _capture_call(
            custom_sql_where="s.study_id = ANY(%s)",
            params=[[1, 2, 3]],
            data_types=["Metagenomic"],
            tags=["GOLD"],
        )
        # Exact expected order per the function's documented (if debated)
        # convention: score_params(none here) + dt_params + tag_params + params.
        assert params == ["Metagenomic", "GOLD", [1, 2, 3]]

    def test_tag_params_land_before_pi_params(self):
        sql, params = _capture_call(
            tags=["GOLD"],
            pi_filter_sql="EXISTS (SELECT 1 FROM unnest(%s::text[]) AS pi_name WHERE sp_pi.name = pi_name)",
            pi_filter_params=[["Jeff Gordon"]],
        )
        assert params == ["GOLD", ["Jeff Gordon"]]

    def test_multiple_tags_all_present(self):
        sql, params = _capture_call(tags=["GOLD", "CURATED"])
        assert params.count("GOLD") == 1
        assert params.count("CURATED") == 1
        # Placeholder count in the tag clause matches param count for those values.
        tag_clause = re.search(r"study_tag IN \(([^)]*)\)", sql).group(1)
        assert tag_clause.count("%s") == 2


class TestSearchStudiesToolPassesTagsThrough:
    def test_tags_extracted_from_args_and_forwarded(self):
        import helpers.agent_tools as agent_tools_mod
        with patch.object(agent_tools_mod, "search_studies_with_sql", return_value=([], "")) as mock_sql, \
             patch.object(agent_tools_mod, "search_studies_by_sample_meta", return_value=[]):
            agent_tools_mod._tool_search_studies({"keywords": ["mouse"], "tags": ["GOLD"]})
        assert mock_sql.called
        assert mock_sql.call_args.kwargs.get("tags") == ["GOLD"]

    def test_tags_omitted_when_not_supplied(self):
        import helpers.agent_tools as agent_tools_mod
        with patch.object(agent_tools_mod, "search_studies_with_sql", return_value=([], "")) as mock_sql, \
             patch.object(agent_tools_mod, "search_studies_by_sample_meta", return_value=[]):
            agent_tools_mod._tool_search_studies({"keywords": ["mouse"]})
        assert mock_sql.call_args.kwargs.get("tags") is None
