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


def _header_row(sid, via=None):
    row = {
        "study_id": sid, "study_title": f"Study {sid}", "study_abstract": "a" * 50,
        "study_alias": f"S{sid}", "metadata_complete": True,
        "pi_name": "PI", "pi_email": "pi@x.org", "pi_affiliation": "X",
        "lab_person_name": None, "num_samples": 10, "data_types": "16S", "num_preps": 1,
    }
    if via:
        row["via"] = via
    return row


class TestFullResultSetInUiPayload:
    """The LLM text and in-chat cards get the trimmed top-`limit`; the full
    ranked set rides ui_payload (all_result_studies/total_matches) for the
    search results panel."""

    def _run(self, n_text=5, n_sample=30, limit=10):
        import helpers.agent_tools as agent_tools_mod
        text_rows   = [_header_row(i) for i in range(1, n_text + 1)]
        sample_rows = [_header_row(i, via="sample_metadata") for i in range(100, 100 + n_sample)]
        # finalize_search_results hits Postgres for sample-layer scoring —
        # replace with a rank-preserving fake honoring the limit contract.
        fake_finalize = lambda studies, kws, resolved_pis=None, veto_applied=False, limit=None, **kw: (
            list(studies)[:limit] if limit else list(studies))
        with patch.object(agent_tools_mod, "search_studies_with_sql", return_value=(text_rows, "SQL")) as mock_sql, \
             patch.object(agent_tools_mod, "search_studies_by_sample_meta", return_value=sample_rows), \
             patch.object(agent_tools_mod, "finalize_search_results", side_effect=fake_finalize):
            res = agent_tools_mod._tool_search_studies({"keywords": ["mouse"], "limit": limit})
        self._last_sql_call = mock_sql.call_args
        return res

    def test_result_studies_trimmed_but_all_result_studies_full(self):
        res = self._run(n_text=5, n_sample=30, limit=10)
        ui = res.ui_payload
        assert len(ui["result_studies"]) == 10
        assert len(ui["all_result_studies"]) == 35
        assert ui["total_matches"] == 35

    def test_full_set_starts_with_the_trimmed_top(self):
        res = self._run(limit=10)
        ui = res.ui_payload
        top_ids = [s["study_id"] for s in ui["result_studies"]]
        assert [s["study_id"] for s in ui["all_result_studies"][:10]] == top_ids

    def test_llm_text_covers_only_the_top_limit(self):
        res = self._run(n_text=5, n_sample=30, limit=10)
        assert "top 10 of 35 matching studies" in res.text
        # Study ids 110+ rank below the trim line — the model must not see them.
        assert "Study 125" not in res.text

    def test_result_summary_reports_the_split(self):
        res = self._run(n_text=5, n_sample=30, limit=10)
        assert res.ui_payload["result_summary"] == "top 10 of 35 studies"

    def test_no_split_summary_when_everything_fits(self):
        res = self._run(n_text=3, n_sample=2, limit=10)
        ui = res.ui_payload
        assert ui["result_summary"] == "5 studies"
        assert ui["total_matches"] == 5
        assert len(ui["all_result_studies"]) == 5

    def test_limit_is_hard_capped_at_10(self):
        res = self._run(n_text=5, n_sample=30, limit=20)
        ui = res.ui_payload
        assert len(ui["result_studies"]) == 10
        assert len(ui["all_result_studies"]) == 35

    def test_sql_overfetch_floor_is_decoupled_from_chat_cap(self):
        self._run(limit=10)
        # max(40, limit * 2) — the panel's text-search half must not shrink
        # because the chat slice is capped at 10.
        assert self._last_sql_call.kwargs.get("limit") == 40

    def test_llm_text_names_total_and_results_panel_when_trimmed(self):
        res = self._run(n_text=5, n_sample=30, limit=10)
        assert "top 10 of 35 matching studies" in res.text
        assert "results panel" in res.text
        assert "View all 35" in res.text

    def test_llm_text_plain_when_not_trimmed(self):
        res = self._run(n_text=3, n_sample=2, limit=10)
        assert "top 5 studies" in res.text
        assert "results panel" not in res.text


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
