"""Structural parity tests — no LLM, no HTTP.

These import service functions directly and call Qiita Postgres.
They verify that the shared SQL helper enforces visibility consistently,
regardless of which planner built the WHERE clause.

Requires: Qiita Postgres reachable (same env as running the backend).
Does NOT require barnacle to be running.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

BLOCKED_STUDY_IDS = [16084]
DISCOVERABLE_CASES = [
    ({"keywords": ["wild", "mice", "shotgun", "metagenomic"]}, 11043),
    ({"keywords": ["wild", "mice", "metagenomics", "WGS", "feral"]}, 11043),
]


@pytest.mark.e2e
class TestVisibilityFilterBlocked:
    """3.1 — search_studies_with_sql enforces visibility for blocked studies."""

    @pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
    def test_direct_sql_blocks_non_public(self, study_id):
        from services.study_service import search_studies_with_sql

        results = search_studies_with_sql(
            custom_sql_where="s.study_id = %s",
            params=[study_id],
        )
        assert results == [], (
            f"Expected no results for non-public study {study_id}, got: {results}"
        )


@pytest.mark.e2e
class TestVisibilityFilterDiscoverable:
    """3.2 — search_studies_with_sql returns public studies directly."""

    def test_direct_sql_finds_11043(self):
        from services.study_service import search_studies_with_sql

        results = search_studies_with_sql(
            custom_sql_where="s.study_id = %s",
            params=[11043],
        )
        assert len(results) == 1, (
            f"Expected study 11043 in results, got: {results}"
        )
        assert results[0]["study_id"] == 11043


@pytest.mark.e2e
class TestBuildWhereFromPlanRespectsVisibility:
    """3.3 — build_where_from_plan → search_studies_with_sql respects visibility."""

    @pytest.mark.parametrize("plan,expected_id", DISCOVERABLE_CASES)
    def test_plan_where_finds_expected(self, plan, expected_id):
        from services.study_service import build_where_from_plan, search_studies_with_sql

        where, params = build_where_from_plan(plan)
        results = search_studies_with_sql(custom_sql_where=where, params=params, limit=150)
        ids = {r["study_id"] for r in results}
        assert expected_id in ids, (
            f"Expected {expected_id} in plan-based search results. "
            f"Plan: {plan}. Got IDs (sample): {sorted(ids)[:20]}"
        )

    @pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
    def test_plan_where_excludes_blocked(self, study_id):
        from services.study_service import build_where_from_plan, search_studies_with_sql

        # Use a broad plan that would return many studies
        plan = {"keywords": ["microbiome", "gut", "bacteria", "human", "host"]}
        where, params = build_where_from_plan(plan)
        results = search_studies_with_sql(custom_sql_where=where, params=params, limit=150)
        ids = {r["study_id"] for r in results}
        assert study_id not in ids, (
            f"Non-public study {study_id} appeared in plan-based search results"
        )


@pytest.mark.e2e
class TestLlmQueryToSqlRespectsVisibility:
    """3.4 — llm_query_to_sql → search_studies_with_sql respects visibility."""

    def test_frontend_planner_finds_11043(self):
        from services.llm import llm_query_to_sql
        from services.study_service import search_studies_with_sql

        plan = llm_query_to_sql("shotgun metagenomic wild mice")
        where = plan.get("where_clause") or "1=1"
        params = plan.get("params") or []
        lim = plan.get("search_limit", 50)

        results = search_studies_with_sql(custom_sql_where=where, params=params, limit=lim)
        ids = {r["study_id"] for r in results}
        assert 11043 in ids, (
            f"Frontend planner did not surface study 11043 for 'shotgun metagenomic wild mice'. "
            f"WHERE: {where} | params: {params} | IDs (sample): {sorted(ids)[:20]}"
        )

    @pytest.mark.parametrize("study_id", BLOCKED_STUDY_IDS)
    def test_frontend_planner_excludes_blocked(self, study_id):
        from services.llm import llm_query_to_sql
        from services.study_service import search_studies_with_sql

        plan = llm_query_to_sql("microbiome gut bacteria human")
        where = plan.get("where_clause") or "1=1"
        params = plan.get("params") or []
        lim = plan.get("search_limit", 50)

        results = search_studies_with_sql(custom_sql_where=where, params=params, limit=lim)
        ids = {r["study_id"] for r in results}
        assert study_id not in ids, (
            f"Non-public study {study_id} appeared in frontend planner search results"
        )
