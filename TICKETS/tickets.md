# Active Tickets

> Known bugs and issues requiring attention.

---

---

## TKT-002: Silent Exception Handling Makes Debugging Difficult

**Severity:** Medium
**Status:** Open

### Description

Several `except Exception:` blocks swallow errors with `pass` or a silent fallback return, with no logging. When failures occur, debugging is hard because errors disappear.

### Affected Files (verified 2026-06-21)

- `qiita_explore/backend/routes/project_routes.py:42` — study-detail fetch/cache failure: `except Exception: pass` (user-facing study-add path)
- `qiita_explore/backend/routes/project_routes.py:69` — sample-context fetch failure: `except Exception: pass`
- `qiita_explore/backend/store/db.py:45` — bucket parse failure: silent `return []`
- `qiita_explore/backend/store/db.py:202,207,212,217,222,228` — migration / ALTER TABLE failures swallowed
- `qiita_explore/backend/store/cache.py:107` — cache parse failure swallowed
- `qiita_explore/backend/store/crud.py:71` — silent swallow
- `qiita_explore/backend/store/merge_crud.py:22` — silent swallow

### Plan

- Add `logger.exception()` / `logger.warning()` to each block above
- For user-facing paths (Qiita fetch in `project_routes.py`), keep the fallback but log so the failure is visible in barnacle logs
- Prioritize `project_routes.py` (user-facing) first

---

## TKT-003: Undefined Variables on Qiita Fetch Failure

**Severity:** Medium
**Status:** Resolved

### Description

If Qiita fetch failed during study add, `preps`/`artifacts` could be unset, risking `NameError` downstream.

### Resolution (verified 2026-06-21)

`preps = []` is now initialized before the try block at `qiita_explore/backend/routes/project_routes.py:34`, and `artifacts` is only used inside the same try (for caching at :41), never downstream. Downstream guards on `if preps:` (:48). NameError risk eliminated. The remaining silent `except Exception: pass` at :42 is tracked under TKT-002.

---

## TKT-004: Race Condition in SSE Response (pin after "done")

**Severity:** Low
**Status:** Open

### Description

In the SSE handlers for global and project chat, if `pin_study_to_chat` fails *after* the SSE "done" message is already yielded, the error only logs and the user sees incorrect pinned-study state.

### Affected Files

- `qiita_explore/backend/routes/global_chat_routes.py` (SSE done handler)
- `qiita_explore/backend/routes/chat_routes.py` (SSE done handler)

### Plan

- Perform the pin operation before yielding the SSE "done" event (do work, then respond)
- If a pin fails after the response is sent, `logger.error()` for monitoring
- Consider client-side reconciliation of pinned state

---

## TKT-005: LLM Chat Context Retention (re-validate under agentic path)

**Severity:** Medium
**Status:** Open (likely addressed — needs verification)

### Description

Originally: the model forgot earlier filters across turns (e.g. "wild mouse studies" then "shotgun studies" dropped the mouse filter). Framed as a Gemma context-window limitation.

### Update (2026-06-21)

This predates the agentic tool-calling loop. Global chat now uses the OpenAI tool-calling path with full conversation history for tool-capable models (`qwen3` is the default; see `helpers/agent.py`, `config.model_supports_tools`). The original Gemma-specific framing is outdated.

### Plan

- Manually re-test the multi-turn filter scenario on the agentic path (`qwen3`)
- If context is retained, close this ticket
- If not, investigate history truncation vs `context_budget_chars(model)` in `config.py`

---

## TKT-006: Pin Studies in Chat Bar + Enter to Start Global Chat

**Severity:** Medium
**Status:** Resolved (core flows) — see caveat

### Description

From the Browse view, let users use the bottom composer: pin studies and press Enter to start a new global chat in one step.

### Resolution (verified 2026-06-21)

- Composer is enabled on browse: `canSend = (isChat || view.type === 'browse') && input.trim().length > 0 && !sending` (`qiita_explore/frontend/js/app_state.js:581`); composer no longer muted on browse (`qiita_explore/frontend/js/app_render.js:523`)
- Enter from browse creates/sends into a global chat: browse branch in `sendMessage` (`qiita_explore/frontend/js/app_state.js:380`)
- Pinning supported via `/pin <ids>` command → `pin_study_ids` payload (`app_state.js:367`)

### Caveat

The "pin directly from a study card / context chip" UX (vs. the `/pin` command) was not separately verified. If that interaction is still desired, open a focused follow-up ticket; otherwise close fully.

---

## TKT-007: Refactor Away from qiita_db.TRN / qiita_core (then delete both packages)

**Severity:** Low
**Status:** Open

### Description

The live app depends on two files outside `qiita_explore/`:

- `qiita_db/sql_connection.py` — the `TRN` PostgreSQL transaction context manager
- `qiita_core/configuration_manager.py` — pulled in transitively by `sql_connection.py`

Everything else in `qiita_db/` (~15 MB, 80+ modules) and `qiita_core/` is dead weight in the repo.

### Affected Files (verified 2026-06-21 — 4 importers, not 3)

- `qiita_explore/backend/routes/study_routes.py`
- `qiita_explore/backend/helpers/qiita_fetch.py`
- `qiita_explore/backend/helpers/sample_search.py`
- `qiita_explore/backend/services/study_service.py`

### Plan

- Replace `TRN` with a raw `psycopg2` connection or a thin local wrapper (template in commit `ed3fc3d8`)
- Remove `qiita_db` / `qiita_core` imports from the 4 files
- Verify tests pass and no `ImportError` at startup
- `git rm -r qiita_db/ qiita_core/`

---

## TKT-009: DuckDB / MIINT

**Severity:** Low
**Status:** Needs scoping

### Description

⚠️ The original description for this ticket was lost — the body had been accidentally duplicated from TKT-007. Only the title ("DuckDB, MIINT") survives. The real scope (what DuckDB and MIINT are meant to address here) needs to be re-written by the author before this can be actioned.

---

## TKT-010: BIOM Ingestion + Diversity Analysis

**Severity:** Medium
**Status:** Open

### Description

The chat advertises a `compute_diversity` tool, but it is a hard stub — `qiita_explore/backend/helpers/agent_tools.py:538` returns *"Diversity analysis is not yet available."* This is the analysis payoff of the merge workflow: a user can merge BIOMs but cannot then analyze the result.

### Plan

- Ingest BIOM/OTU tables from a merge result bundle (merge already emits `result.tar.gz`; see `helpers/merge_executor.py`)
- Implement `_tool_compute_diversity` to compute alpha/beta metrics: Shannon, Faith's PD, Bray-Curtis, UniFrac
- Emit a `segment_tool_result` with a renderable `ui_payload` (new branch in `ToolResultWidget`, frontend `components.js`)
- Coordinate with TKT-015 — diversity is only meaningful once the merge execution path is trustworthy

### Files Changed

- `qiita_explore/backend/helpers/agent_tools.py`
- `qiita_explore/frontend/js/components.js`

---

## TKT-011: Split Oversized Files (500-line cap)

**Severity:** Low
**Status:** Partially resolved

### Description

Files over the 500-line `qiita_explore/` cap (verified 2026-06-21):


| File                             | Lines | Tracked by |
| -------------------------------- | ----- | ---------- |
| `frontend/js/components.js`      | 630   | TKT-011    |
| `frontend/js/app_state.js`       | 626   | TKT-011    |
| `frontend/js/app_render.js`      | 570   | TKT-011    |
| `backend/helpers/agent_tools.py` | 548   | TKT-013    |
| `backend/helpers/qiita_fetch.py` | 532   | TKT-011    |
| `backend/routes/merge_routes.py` | 528   | TKT-014    |
| `backend/store/crud.py`          | 515   | TKT-011    |

### Resolution (verified 2026-07-14)

- `qiita_fetch.py`: 532 → 486 via query consolidation (TKT-029/030), **under cap**, no split needed (closes as TKT-040).
- `crud.py`: 515 → 412 via `global_chat_crud.py` extraction (TKT-015 predates this pass), **under cap**.
- `merge_routes.py`: 528 → 364 via `helpers/merge_helpers.py` extraction (this pass, see TKT-014), **under cap**.
- `components.js` (684), `app_state.js` (633), `app_render.js` (601), `agent_tools.py` (548) **remain over cap** — see TKT-038, TKT-036, TKT-037, TKT-013 respectively. The `app_actions.js` split proposed below for `app_state.js` was not done as a file split, but a chunk of its duplication was removed in-place (`streamChat`/`createProjChatAndSeed`/`createGlobalChatAndSeed`, see TKT-036).

### Plan (TKT-011 scope, remaining files only)

**components.js / app_render.js / agent_tools.py** — split along feature boundaries (TBD per file; see TKT-038, TKT-037, TKT-013).

---

## TKT-012: Merge Page Request Fan-outs

**Severity:** Low
**Status:** Open

### Description

Two spots in the merge page fan out parallel requests that could become expensive at scale:

1. `**MergesTab` mount** (`frontend/js/merge_workspace.js`): on load, fetches full workspace detail for every workspace in parallel via `Promise.all(list.map(...))`.
2. `**GlobalBiomSelector` Smart Select** (`frontend/js/merge_artifacts.js`, `handleApply`): fetches all missing study details in parallel (up to 5), no rate limiting / cancellation.

### Plan

- Lazy-load workspace detail in `MergesTab` on expand/hover (or batch on a short delay)
- In `GlobalBiomSelector`, fetch sequentially or add a concurrency limit

---

## TKT-013: Split agent_tools.py (over 500-line cap)

**Severity:** Low
**Status:** Open

### Description

`qiita_explore/backend/helpers/agent_tools.py` is 548 lines (verified 2026-06-21), over the cap.

### Plan

Extract tool implementations into `helpers/agent_tool_impls.py`:

- Move `_tool_search_studies`, `_tool_search_by_sample`, `_tool_get_study_report`, `_tool_pin_study`, `_tool_compute_diversity`
- Keep `TOOL_SCHEMAS`, `ToolResult`, `execute_tool`, and small helpers in `agent_tools.py`

---

## TKT-014: Split merge_routes.py (over 500-line cap)

**Severity:** Low
**Status:** Resolved (via helper extraction, not the route split)

### Description

`qiita_explore/backend/routes/merge_routes.py` is 528 lines (verified 2026-06-21), over the cap.

### Resolution (verified 2026-07-14)

Extracted the shared, pure (no-Flask) helpers `_get_artifacts`, `_type_filtered_artifacts`, `_resolve_artifact`, `_get_sample_ids` into new `helpers/merge_helpers.py`, imported by both `merge_routes.py` and `artifact_routes.py` — this also fixed a route-module-importing-from-route-module coupling (`artifact_routes.py` previously did `from routes.merge_routes import _get_artifacts`). This dropped `merge_routes.py` to 364 lines, **under the 500-line cap**, so the originally-proposed `merge_workspace_routes.py`/`merge_job_routes.py` route split is no longer needed.

### Files

- New: `qiita_explore/backend/helpers/merge_helpers.py`
- `qiita_explore/backend/routes/merge_routes.py`
- `qiita_explore/backend/routes/artifact_routes.py`

---

## TKT-015: Merge Executor Shipped in Dev-Only Mode

**Severity:** High
**Status:** Open

### Description

`qiita_explore/backend/helpers/merge_executor.py:3` carries a `TODO (before merging to master): replace _run_local with SFTP+SSH pipeline` — but the multi-LLM branch was merged to master with the local path still in place. The executor runs `conda run -n qiita python scripts/remote_merge.py` as a local subprocess (`merge_executor.py:100`), which only works if the serving host has the `qiita` conda env, the script, and local access to the BIOM files. On a real/remote deployment the merge job will fail.

### Plan

Choose one:

- **Finish the remote pipeline** — implement the paramiko SFTP+SSH path described in the TODO (upload BIOMs → ssh exec → download `result.tar.gz`); keep the same `on_status` interface.
- **Or gate/document local-only mode** — explicitly require the local `qiita` env and surface a clear error when prerequisites are missing, so master doesn't silently fail.

### Files

- `qiita_explore/backend/helpers/merge_executor.py`

---

## TKT-016: Parallelize Header Enrichment + Reuse Connection Pool

**Severity:** Medium
**Status:** Open

### Description

After the parallel sample probes complete, study headers are fetched in a plain sequential loop — `sample_search.py:200` (field-filter path) and `qiita_fetch.py:359` (pin path). Additionally, a fresh `ThreadedConnectionPool` is created on every search call (`sample_search.py:165`, `:229`), adding connection-setup latency to every request.

### Plan

- Fan out `_fetch_study_header` calls using the `ThreadPoolExecutor` already in scope; collect results with `as_completed`.
- Hoist the `ThreadedConnectionPool` to module scope in `sample_search.py`; size it to `pool_size` (default 16). Destroy + recreate only on connection errors.

### Files

- `qiita_explore/backend/helpers/sample_search.py:165`, `:200`, `:229`
- `qiita_explore/backend/helpers/qiita_fetch.py:359`

---

## TKT-017: Shared Cross-Worker Study Header Cache

**Severity:** Low
**Status:** Open

### Description

`_study_header_cache` is an in-process dict (`qiita_fetch.py:271`). Gunicorn spawns 4 workers; each worker cold-starts its own empty cache and re-fetches headers the first time it serves a request. The SQLite `study_detail_cache` table is already shared across workers and restarts.

### Plan

- Promote `_fetch_study_header_cached` to write through to the shared SQLite `study_detail_cache` (add a `header_json` TEXT column or reuse `detail_json`). Keep the in-process dict as an L1 TTL for the current worker.
- Schema migration required (`db.py` migrations list).

### Files

- `qiita_explore/backend/helpers/qiita_fetch.py:270–285`
- `qiita_explore/backend/store/db.py` (migration), `store/cache.py`

---

## TKT-018: Stream Sample-Search Results Incrementally

**Severity:** Medium
**Status:** Open

### Description

`search_studies_by_sample_meta` and `field_filter_search` block until all probes finish (or timeout), then return all matches at once. Users see nothing until the full scan completes. The SSE plumbing (`stream_agent` / `_execute_tool_call`) already supports streaming partial results per-event.

### Plan

- Refactor the probe loops to yield matched study IDs as `as_completed` fires them.
- Emit each batch as a `segment_tool_result_partial` SSE event (or reuse `token` events with a structured payload); frontend accumulates into the existing `InlineStudyCard` grid progressively.

### Files

- `qiita_explore/backend/helpers/sample_search.py:174–205`, `:239–260`
- `qiita_explore/backend/helpers/agent.py` (`_execute_tool_call`)
- `qiita_explore/frontend/js/components.js` (`ToolResultWidget`)

---

## TKT-019: Avoid Redundant Agent Synthesis Round

**Severity:** Low
**Status:** Open

### Description

When the tool-call loop ends on tool results and `final_had_synthesis` is False, a full extra LLM completion is fired (`agent.py:141–153` for Anthropic path, `:307–317` for OpenAI path). This adds latency on every agentic search. It is often avoidable: if the last tool result already contains enough context for the model to reply inline, we can prompt it to do so within the main loop rather than appending a synthesis pass.

### Plan

- Append a short system nudge before the last tool-result turn: *"Reply directly based on the results above. Do not call another tool."*
- Detect whether the model synthesized within the loop (`final_had_synthesis = True`) and skip the extra completion.
- Benchmark: compare round-trip latency with/without the extra pass on 10 representative queries.

### Files

- `qiita_explore/backend/helpers/agent.py:120–153`, `:275–317`

---

## TKT-020: Optional Production JS Precompile

**Severity:** Low
**Status:** Open

### Description

~4,000 lines across 10 Babel-type=text/babel script tags are transpiled in the browser on every page load. This is fine for development but adds ~1–2 s to first-paint on cold loads. The no-build dev workflow must be preserved.

### Plan

- Add an optional `build.sh` (or `make prod`) that runs `@babel/cli` over `frontend/js/*.js` into `frontend/js/dist/` and outputs a single `app.bundle.js` with cache-bust hash.
- `index.html` detects `?prod=1` (or a separate `index.prod.html`) and loads the bundle instead of the individual Babel-annotated scripts.
- Dev path unchanged: open `index.html` directly.

### Files

- `qiita_explore/frontend/index.html`
- New: `qiita_explore/frontend/build.sh`

---

## TKT-021: Metadata Cohort Builder + Export

**Severity:** Medium
**Status:** Open

### Description

Users want to filter samples across pinned studies by a shared metadata field (e.g. `host_scientific_name`, `body_site`, `empo_3`) and export the resulting sample set as CSV or a filtered BIOM bundle. This complements the merge workflow but is useful standalone for hypothesis generation.

### Plan

1. New backend route `POST /api/cohort/filter` — accepts `chat_id`, `field`, `values[]`; queries `sample_{study_id}` JSONB across pinned studies in parallel (reuse `field_filter_search` probe pattern); returns matched sample rows.
2. New backend route `GET /api/cohort/export?chat_id=&format=csv|biom` — streams the CSV/BIOM.
3. New agent tool `build_cohort` — wraps the filter route; result UI shows a summary + download button.
4. Frontend: `CohortBubble` component rendered by `ToolResultWidget` when `payload.kind === 'cohort'`.

### Files

- `qiita_explore/backend/routes/` (new `cohort_routes.py`)
- `qiita_explore/backend/helpers/agent_tools.py` (new tool)
- `qiita_explore/frontend/js/components.js` (`CohortBubble`)

---

## TKT-022: Cross-Study Metadata Field-Overlap Matrix

**Severity:** Low
**Status:** Open

### Description

Before merging studies, users need to know which sample metadata fields are shared across them. Currently there is no tool for this. A field-overlap matrix (studies × fields, colored by coverage %) would directly inform merge decisions.

### Plan

1. New agent tool `field_overlap` — accepts `study_ids[]`; queries `information_schema.columns` for `sample_{id}` tables or probes `sample_values` JSONB keys; returns a dict `{field → {study_id → pct_non_null}}`.
2. Frontend: `FieldOverlapMatrix` component — renders a heatmap-style table; highlights fields present in ≥80% of all studies as merge-ready candidates.
3. Wire into `InlineStudyCard` "Compare fields" action (or a slash command `/overlap 104 77 101`).

### Files

- `qiita_explore/backend/helpers/agent_tools.py`
- `qiita_explore/backend/helpers/sample_search.py` or new `helpers/cohort.py`
- `qiita_explore/frontend/js/components.js` (`FieldOverlapMatrix`)

---

## TKT-023: Merge Autopick Doesn't Check Deprecated / Human-Filtering / Primer Compatibility

**Severity:** High
**Status:** Open

### Description

Inspected real `prep_template` rows and `sample_values` JSONB (2026-07-01). `biom_autopick.py` only
filters candidate artifacts by `data_type` namespace (e.g. "16S") when auto-picking/validating a
merge. It does not check:

- `prep_template.deprecated` — a deprecated prep can be silently selected for a merge
- `prep_template.current_human_filtering` — a human-filtered prep can be merged with an
  unfiltered one, silently mixing incomparable read sets
- `target_gene` / `target_subfragment` / `platform` — these live in per-sample `sample_values`
  JSONB (Qiita duplicates prep-level fields onto every sample row) and are the real fine-grained
  compatibility signal. Two "16S" preps sequenced with different primers (e.g. V3 vs V4) pass the
  current namespace-only check but produce a biologically meaningless merged feature table.

### Plan

- Pull `deprecated`, `current_human_filtering`, `target_gene`, `target_subfragment`, `platform`
  alongside the existing prep/artifact fields in `qiita_fetch._fetch_study_detail_from_qiita`
- Enforce in `biom_autopick.py`'s compatibility check: exclude `deprecated` preps by default,
  block mixing `current_human_filtering` states, and treat `target_gene`/`target_subfragment`/
  `platform` mismatches the same as a `data_type` mismatch
- Not blocking the MIINT migration — this is a correctness gap in the *current* merge code,
  independent of whether/when MIINT replaces it

### Files

- `qiita_explore/backend/helpers/biom_autopick.py`
- `qiita_explore/backend/helpers/qiita_fetch.py` (`_fetch_study_detail_from_qiita`)

---

## TKT-024: `/api/search` Latency Variance (86ms–13.5s) — Unindexed Leading-Wildcard ILIKE

**Severity:** Medium
**Status:** Open

### Description

Benchmarked via `qiita_explore/backend/tests/benchmarks/search_latency.py` and `concurrent_bench.py`
(2026-07-04). Identical/similar queries against `/api/search` range from 86ms to 13.5+ seconds,
and 3/15 representative queries timed out outright (>10s).

Root cause, confirmed by reading `search_studies_with_sql`
(`qiita_explore/backend/services/study_service.py:148-244`): the query computes 4 correlated
subqueries per row (`num_samples` COUNT, `data_types` STRING_AGG via a 2-join, `num_preps` COUNT
DISTINCT, `is_gold` EXISTS) plus a relevance score, under `SELECT DISTINCT ... ORDER BY relevance
DESC`, with `LIMIT`/`OFFSET` applied last. Postgres can't push `LIMIT` below a sort on a computed
column, so it materializes and scores **every** row matching `WHERE` before trimming — latency
scales with total matching-row count, not the 50-150 row cap.

Compounding this: every text predicate (`_keyword_clause_sql` in `services/llm.py:24-28`,
`build_relevance_score` in `study_service.py:125-145`) is `col ILIKE '%term%'` — a leading
wildcard defeats standard B-tree indexes, forcing sequential scans of `qiita.study` joined to
`qiita.study_person` (twice) on every search. Confirmed via `qiita-db-unpatched.sql:4290-4311`
that `study_title`/`study_abstract`/`study_alias` and `study_person.name`/`affiliation` have zero
supporting indexes.

There's already in-repo precedent for the fix: `patches/93.sql:41-54` added
`CREATE EXTENSION IF NOT EXISTS "pg_trgm"` + `GIN(... gin_trgm_ops)` trigram indexes to solve this
exact class of problem for a different table (`processing_job.command_parameters`).

### Plan

- New Postgres patch (e.g. `patches/9X.sql`) adding `pg_trgm` GIN trigram indexes on
  `qiita.study.study_title`, `study_abstract`, `study_alias`, and `qiita.study_person.name`,
  `affiliation`
- Re-run `search_latency.py`/`concurrent_bench.py` before/after to confirm the tail latency and
  timeout rate drop
- Scoped out of the connection-pooling/cache fixes (TKT-\* around the same benchmarks) since this
  touches schema on the externally-managed Qiita Postgres DB — needs a deliberate, separate
  migration rather than an app-code change

### Files

- `qiita_explore/backend/services/study_service.py` (`search_studies_with_sql`, `build_relevance_score`)
- `qiita_explore/backend/services/llm.py` (`_keyword_clause_sql`)
- New: a `patches/*.sql` migration (see `patches/93.sql` for the precedent)

---

## TKT-025: Remove Unused `_qiita_fetch()` Helper — REVISED

**Severity:** Low
**Status:** Invalid — function IS used

### Important Finding (Plan Agent)

After deeper code review, `_qiita_fetch()` at lines 130, 144, 179, 207, 232, 414, 464, 491 **IS actively used** as a thin wrapper around `pooled_fetchall`. It cannot be removed.

**Revised:** Keep `_qiita_fetch()` but the mid-module import cleanup (TKT-028) may reveal opportunities for simplification.

### Files

- `qiita_explore/backend/helpers/qiita_fetch.py`

---

## TKT-026: Remove Redundant In-Memory Study Header Cache

**Severity:** Low
**Status:** Open

### Description

`helpers/qiita_fetch.py:266-279` defines `_study_header_cache` (module-level dict) which conflicts with the more sophisticated SQLite-based caching in `store/cache.py`. This creates two separate caching mechanisms that could lead to inconsistency.

### Plan

Remove:
```python
# REMOVE (lines ~266-279):
_STUDY_HEADER_TTL_SECONDS = 3600
_study_header_cache = {}
def _fetch_study_header_cached(study_id: int):
    """TTL-memoized wrapper around _fetch_study_header (hot path for pinned context)."""
```

Rely solely on `store/cache.py` (`get_study_detail_cache`, `upsert_study_detail_cache`).

**Impact:** ~15 lines removed, complexity reduction

### Files

- `qiita_explore/backend/helpers/qiita_fetch.py`

---

## TKT-027: Remove Legacy `test.ipynb`

**Severity:** Low
**Status:** Resolved

### Description

`qiita_explore/test.ipynb` appears to be a debugging artifact from development. Not part of the current workflow.

### Resolution (verified 2026-07-14)

Deleted (along with other stale `ezredbiom/` leftovers from the directory rename: `DNA Loaders.html`, `logo.png`, `qiita-mark-nobg.png`, `qiita-mark.png` — confirmed unreferenced).

### Files

- `qiita_explore/test.ipynb`

---

## TKT-028: Clean Up Mid-Module Imports in llm_helpers.py

**Severity:** Low
**Status:** Resolved

### Description

`helpers/llm_helpers.py:55-59` has imports placed mid-module (after function definitions) with some unused imports (`get_study_detail_cache`, `upsert_study_detail_cache`).

### Resolution (verified 2026-07-14)

Moved to the top import block. The same issue was found in `helpers/qiita_fetch.py` (a `from store import (...)` and `from helpers.llm_helpers import _truncate` sitting after a module-level assignment) and fixed the same way — confirmed no circular-import breakage (`llm_helpers.py` only imports `qiita_fetch` function-locally, at call time, to avoid a cycle).

### Files

- `qiita_explore/backend/helpers/llm_helpers.py`
- `qiita_explore/backend/helpers/qiita_fetch.py`

---

## TKT-029: Consolidate Duplicate Study Header Queries

**Severity:** Medium
**Status:** Resolved

### Description

`first_studies()` (lines 51-116) and `_fetch_study_header()` (lines 411-459) execute nearly identical SELECT queries with the same JOIN structure, column selections, and WHERE clauses (~40 lines of duplication).

### Resolution (verified 2026-07-14, done prior to this pass)

`_build_study_header_query()` + `_row_to_study_header()` now shared by both call sites; `qiita_fetch.py` dropped from 534 to 486 lines as a result (closes TKT-040 as unnecessary — see below).

### Files

- `qiita_explore/backend/helpers/qiita_fetch.py`

---

## TKT-030: Consolidate Sample Fetch Functions

**Severity:** Low
**Status:** Partially resolved

### Description

`_fetch_study_samples()` (lines 141-173) and `_fetch_full_sample_metadata()` (lines 228-244) have near-identical structure with minor parameter differences.

### Resolution (verified 2026-07-14)

`_fetch_sample_context_text` now delegates to `_fetch_full_sample_metadata` (dedup done). `_fetch_study_samples` and `_fetch_full_sample_metadata` were **not** merged — they return different shapes (3 named JSON keys vs. full `sample_values`); forcing a merge would change behavior at call sites for no real benefit. Closing the "merge into one function" part as won't-do; the actual duplication (the sample-context path) is deduplicated.

### Files

- `qiita_explore/backend/helpers/qiita_fetch.py`

---

## TKT-031: Extract Shared `request_utils.py`

**Severity:** Medium
**Status:** Partially resolved

### Description

`chat_routes.py` and `global_chat_routes.py` have ~150 lines of duplicated logic: SSE pin handling, report study handling, study ID parsing, pinned context building, message normalization, user_id extraction.

### Resolution (verified 2026-07-14)

Created `helpers/request_utils.py` with `parse_chat_stream_body()` (message/model/report_study_id/pin_study_ids parsing + validation), `build_full_msgs()`, `sse_response()` (the shared `Response(..., mimetype='text/event-stream', ...)` tail), and `stream_samples_report()` (the shared "load_samples" SSE step, using the `yield from ...; return (a, b)` idiom already established by `helpers/pin_flow.py`). Wired into both `chat_routes.py` and `global_chat_routes.py`, removing the now-unused `Response`/`stream_with_context` imports from both. **Not** wired into `project_routes.py`/`merge_routes.py` — checked and neither has the same duplicated shape (they don't handle chat-stream bodies or SSE `report_study_id`/`pin_study_ids`), so extending the import list there would just be dead imports.

### Files

- New: `qiita_explore/backend/helpers/request_utils.py`
- `qiita_explore/backend/routes/chat_routes.py`
- `qiita_explore/backend/routes/global_chat_routes.py`

---

## TKT-032: Consolidate SSE Streaming Patterns

**Severity:** Medium
**Status:** Open

### Description

`_stream_anthropic_agent()` and OpenAI streaming in `agent.py` have ~80+ lines of duplicated control flow: same iteration structure, tool execution pattern, final synthesis logic, SSE yield patterns.

### Plan

Extract base streaming class/function with common loop logic. Provider-specific code stays in subclass/method.

**Impact:** ~40 lines saved, improved maintainability

### Files

- `qiita_explore/backend/helpers/agent.py`

---

## TKT-033: Extract `useModelSelection()` Hook

**Severity:** Low
**Status:** Resolved

### Description

Model selection state is spread across `selectedModel`, localStorage (`llm:model`, `model:chat:<id>`), and sync effects in `app_state.js:31-44, 89-98`.

### Resolution (verified 2026-07-14, done prior to this pass)

Extracted to `hooks/useModelSelection.js`; `app_state.js` consumes it via `useModelSelection(view.chatId)`. No leftover inline model-selection state in `app_state.js`.

### Files

- `qiita_explore/frontend/js/app_state.js`
- New: `qiita_explore/frontend/js/hooks/useModelSelection.js`

---

## TKT-034: Consolidate Date Formatting

**Severity:** Low
**Status:** Resolved

### Description

Same date formatting (`toLocaleDateString('en-US', { month: 'short', day: 'numeric' })`) appears 4 times in `app_render.js`.

### Resolution (verified 2026-07-14, done prior to this pass)

`formatDate()` added to `utils.js`; `app_render.js` calls it at both usage sites. Zero remaining inline `toLocaleDateString` calls on dates in `app_render.js`/`components.js`.

### Files

- `qiita_explore/frontend/js/utils.js`
- `qiita_explore/frontend/js/app_render.js`

---

## TKT-035: Simplify Slash Command Matching

**Severity:** Low
**Status:** Resolved

### Description

`useMemo` in `app_state.js:628-632` recomputes on every keystroke unnecessarily.

### Resolution (verified 2026-07-14, done prior to this pass)

Replaced with a plain inline conditional; `useMemo` removed.

### Files

- `qiita_explore/frontend/js/app_state.js`

---

## TKT-036: Split `app_state.js` (672 → ~300 lines)

**Severity:** Low
**Status:** Open (dedup done, split still pending)

### Description

`app_state.js` is 672 lines, over the 500-line cap by 172 lines. As of 2026-07-14: 633 lines (after TKT-033/035 plus a new `streamChat()` + `createProjChatAndSeed()`/`createGlobalChatAndSeed()` extraction that deduplicated the project-chat vs. global-chat `sendMessage` branches). Still over cap — the file split below is unstarted.

### Plan

| New File | Contents | Est. Lines |
|----------|----------|------------|
| `sse_helpers.js` | SSE event handlers, stream transformers | ~150 |
| `loaders.js` | Expand existing: loadProjectDetail, loadGlobalChats, etc. | ~120 |
| `search_helpers.js` | doSearch function | ~80 |
| `chat_actions.js` | newProjChat, deleteProjChat, pinStudy, etc. | ~100 |
| `app_state.js` | Remaining hook (~200 lines) | ~200 |

### Files

- `qiita_explore/frontend/js/app_state.js`
- New split files in `qiita_explore/frontend/js/`

---

## TKT-037: Split `app_render.js` (601 → ~200 lines)

**Severity:** Low
**Status:** Open

### Description

`app_render.js` is 601 lines, over the 500-line cap by 101 lines.

### Plan

| New File | Contents | Est. Lines |
|----------|----------|------------|
| `sidebar_renderer.js` | Sidebar with projects/chats | ~170 |
| `browse_renderer.js` | Browse grid, filters | ~140 |
| `chat_renderer.js` | Chat messages, segments | ~160 |
| `composer_renderer.js` | Input composer, pin bar | ~100 |
| `app_render.js` | Topbar, modals, routing | ~130 |

### Files

- `qiita_explore/frontend/js/app_render.js`
- New split files in `qiita_explore/frontend/js/`

---

## TKT-038: Split `components.js` (689 → ~250 lines)

**Severity:** Low
**Status:** Open

### Description

`components.js` is 689 lines, over the 500-line cap by 189 lines. As of 2026-07-14: 684 lines — still over cap, split unstarted.

### Plan

| New File | Contents | Est. Lines |
|----------|----------|------------|
| `model_picker.js` | Model selection dropdown | ~100 |
| `pinned_bar.js` | Pinned studies UI | ~80 |
| `slash_commands.js` | Command palette | ~120 |
| `components.js` | Remaining core components (~200 lines) | ~200 |

### Files

- `qiita_explore/frontend/js/components.js`
- New split files in `qiita_explore/frontend/js/`

---

## TKT-039: Split `agent_tools.py` (548 → ~250 lines)

**Severity:** Low
**Status:** Open

### Description

`agent_tools.py` is 548 lines, over the 500-line cap by 48 lines.

### Plan

| New File | Contents | Est. Lines |
|----------|----------|------------|
| `agent_tool_schemas.py` | TOOL_SCHEMAS, ToolResult dataclass | ~200 |
| `agent_tool_executors.py` | All _tool_* functions | ~300 |
| `agent_tools.py` | Re-export layer | ~100 |

### Files

- `qiita_explore/backend/helpers/agent_tools.py`
- New split files in `qiita_explore/backend/helpers/`

---

## TKT-040: Split `qiita_fetch.py` (535 → ~200 lines)

**Severity:** Low
**Status:** Closed — not needed

### Description

`qiita_fetch.py` is 535 lines, over the 500-line cap by 35 lines.

### Resolution (verified 2026-07-14)

TKT-029's query consolidation (plus the TKT-028 import hoist) dropped the file to 486 lines — under the 500-line cap without any file split. No further action needed.

### Files

- `qiita_explore/backend/helpers/qiita_fetch.py`

---

## TKT-041: `fresh_db` Test Fixture Doesn't Isolate Route-Level Tests From the Real Local DB

**Severity:** Medium
**Status:** Open

### Description

`tests/conftest.py`'s `fresh_db` fixture (autouse=True) is meant to give every test an isolated
SQLite DB: it sets `QIITA_EXPERIMENT_DB_PATH` to a `tmp_path`, deletes every `sys.modules` entry
whose name contains `'store'`, and reimports `store.db` so `DB_PATH` rebinds to the temp path.

That reimport only fixes modules matched by the `'store' in mod_name` filter. Any module that
imported specific names out of `store` at its own (one-time, collection-time) import —
e.g. `routes/chat_routes.py`: `from store import get_study_detail_cache, upsert_study_detail_cache,
pin_study_to_chat, ...` — keeps a direct reference to the *original* function objects bound to
the *original* `DB_PATH` (the real default `qiita_explore/backend/data/projects.db`, not the fixture's
tmp_path). Route modules are never deleted from `sys.modules` because `'routes.chat_routes'`
doesn't match the `'store'` substring filter.

Practical effect: tests that go through `crud`/`db_conn` fixtures (which freshly `import store.crud`
per-test) are correctly isolated. Tests that exercise real routes via `app.test_client()` (e.g.
`test_api.py`, `test_chats.py`) route through already-imported `routes.*`/`run` modules and can
read/write the real local `projects.db` instead of the intended tmp_path.

Confirmed 2026-07-06: running `pytest tests/ --ignore=tests/e2e` applied a pending
`study_detail_cache` schema migration (`ALTER TABLE ... ADD COLUMN prep_metadata_json/
samples_json/total_samples`, already coded in `store/db.py:221-231`) against the real
`qiita_explore/backend/data/projects.db` rather than an isolated fixture DB. In that instance it was
harmless (additive migration, no row data changed — confirmed via full `sqlite3 .dump` diff), but
the same gap could let a test **write** fixture data into the user's real local project DB.

### Plan

- Either: reload every already-imported module that holds direct `from store import X` bindings
  (not just modules matching `'store' in mod_name`), or
- Simpler: don't rely on deleting `sys.modules` at all — have `store/db.py` read `DB_PATH` lazily
  (a function call, not a module-level constant) so `monkeypatch.setenv` is sufficient on its own
- Add a regression test: assert a route-level test (via `app.test_client()`) writing to the DB
  does not appear in the real `qiita_explore/backend/data/projects.db` after the test session

### Files

- `qiita_explore/backend/tests/conftest.py` (`fresh_db` fixture)
- `qiita_explore/backend/store/db.py:12` (`DB_PATH` module-level constant)

---

## TKT-042: `/api/settings` Is Global, Not Per-User

**Severity:** High
**Status:** Open

### Description

The settings endpoints are not scoped per user. Any authenticated user who saves an Anthropic
API key overwrites the key for **every other user**, and subsequent Anthropic requests are then
billed against whichever key was written last.

`routes/study_routes.py :: api_get_settings` and `api_post_settings` call
`store/crud.py :: get_setting` / `set_setting`, which read and write the shared `meta` table
(`SELECT value FROM meta WHERE key = ?` / `INSERT INTO meta(key,value) ... ON CONFLICT DO UPDATE`).
Neither function takes a `user_id` parameter. `config.py :: get_client` then resolves the Anthropic
key via `get_setting('anthropic_api_key')` with no user context.

This is pre-authentication code that was not re-scoped when multi-user auth landed (`6ddc3890`,
Jul 2026). Every other ownership-scoped query in the app filters on `g.user_id` in the WHERE clause
(`get_project`, `get_global_chat`, `get_workspace`, `get_merge_job`); settings were missed.

Verified 2026-07-18 by reading `crud.py` and both route handlers.

### Plan

- Preferred: add a `user_settings(user_id, key, value)` table with PK `(user_id, key)`, and give
  `get_setting`/`set_setting` a required `user_id` argument
- Keep the existing `meta` table for genuinely global markers — it also stores `tinydb_imported`
  and the legacy-claim marker (`store/legacy_claim.py`), so do **not** blanket-add a `user_id`
  column to it
- Thread the caller's `user_id` into `config.py :: get_client`, or change how it resolves the key
- Note: `meta.anthropic_api_key` is currently stored in **plaintext**, in the same database whose
  `auth_sessions.pat_encrypted` column is Fernet-encrypted. Worth encrypting as part of this work
- Add a test: user A sets a key; user B's `GET /api/settings` does not report it set; B setting a
  key does not disturb A's

### Files

- `qiita_explore/backend/routes/study_routes.py` (`api_get_settings`, `api_post_settings`)
- `qiita_explore/backend/store/crud.py` (`get_setting`, `set_setting`)
- `qiita_explore/backend/config.py` (`get_client`)
- `qiita_explore/backend/store/db.py` (`meta` table DDL)
- Documented in `docs/02-authentication.md` § "Two places where tenancy does not hold"

---

## TKT-043: Artifact File Download Performs No Study-Level Authorization

**Severity:** High
**Status:** Open

### Description

`routes/artifact_routes.py :: download_artifact_file` (`GET /api/artifacts/<artifact_id>/files/
<filepath_id>/download?study_id=<id>`) requires a session, then takes `study_id`, `artifact_id`,
and `filepath_id` straight from the request, resolves a path via `_resolve_artifact_file`, and
`send_file`s it.

It never calls `is_study_public(study_id)`. Compare `routes/study_routes.py :: api_study_detail`,
which gates on exactly that before returning study detail. There is also no ownership check —
artifacts are not owned by QiitaExplore users — so nothing constrains which study a caller may
read from.

Practical effect: any authenticated user can enumerate study/artifact/filepath id triples and
download files belonging to studies that are **not public**.

What the existing check does and does not do: `_resolve_artifact_file` resolves paths from the
artifact graph rather than from raw user input, calls `os.path.realpath`, verifies
`os.path.isfile`, and rejects a blocklist of roots (`_FORBIDDEN_ROOTS = ('/etc/', '/proc/',
'/sys/', '/dev/', '/root/')`). That is a reasonable directory-traversal defense. It is not an
access-control check, and was not intended as one.

Verified 2026-07-18 by reading the handler and comparing against `api_study_detail`.

### Plan

- Add an `is_study_public(study_id)` gate at the top of the handler, returning **404** (not 403 —
  do not leak existence) when it fails. `is_study_public` already exists in `helpers/qiita_fetch.py`
- Replace the `_FORBIDDEN_ROOTS` blocklist with an **allowlist**: resolve the realpath and require
  it to sit under `QIITA_BASE_DATA_DIR`. A blocklist of five system directories does not enumerate
  everything that should be off-limits
- Check whether `get_artifact_samples` and `get_artifact_sample_counts` in the same module need the
  same gate
- Minor, same file: the "File not found on disk" case currently returns 403 via the `ValueError`
  handler; 404 is the correct status
- Add tests: a non-public study's artifact returns 404 for an authenticated user; a path escaping
  the data root is rejected; a legitimate public-study download still succeeds

### Files

- `qiita_explore/backend/routes/artifact_routes.py` (`download_artifact_file`,
  `_resolve_artifact_file`, `_FORBIDDEN_ROOTS`)
- `qiita_explore/backend/helpers/qiita_fetch.py` (`is_study_public`)
- Documented in `docs/02-authentication.md` § "Two places where tenancy does not hold"

---

## TKT-044: Merge Can Silently Substitute a Different Artifact Than the One Selected

**Severity:** High
**Status:** Open

### Description

An explicit user artifact selection can be silently discarded and replaced by autopick, so a merge
runs on a different artifact than the one the user ticked — and reports success.

**Root cause: there are two artifact lists**, built by different queries and cached in different
columns.

- `helpers/artifact_graph.py :: fetch_artifact_graph` → cached as `artifact_graph_json`. Covers all
  of `qiita.study_artifact`, with parent edges, job nodes, per-file lists, and BFS-propagated
  `data_type`. This is what the **tree UI renders** and what the user selects BIOMs from.
- `helpers/qiita_fetch.py :: _fetch_study_detail_from_qiita` → cached as `artifacts_json`, reached
  via `helpers/merge_helpers.py :: _get_artifacts`. Prep-joined, `LIMIT 500`, `data_type` read
  straight off the prep with no propagation. This is what **autopick, validate and submit** resolve
  against.

In both `helpers/merge_helpers.py :: _resolve_artifact` and the submit handler in
`routes/merge_routes.py`, the pattern is:

```python
chosen = [a for a in artifacts if a["artifact_id"] in chosen_ids]
```

When `chosen` comes back empty it falls through to `autopick_artifact(...)` with no warning
returned to the caller. A BIOM present in the graph but absent from the prep-joined list — beyond
`LIMIT 500`, or not reachable through a prep join — means the user's explicit selection is dropped
and a different artifact is merged.

Given TKT-023 (autopick ignores primer/deprecation compatibility), the substituted artifact may not
even be biologically comparable.

Verified 2026-07-18 while documenting the merge subsystem.

### Plan

- Make an unresolvable explicit selection a **hard error**, not a silent fallback. If `chosen_ids`
  is non-empty and resolves to nothing, return 4xx from validate/submit naming the artifact ids
  that could not be resolved. Autopick should apply only when the user has chosen nothing
- Longer term, reconcile the two lists so the UI and the resolver agree on what exists. The graph is
  the more complete source; consider resolving explicit selections against `artifact_graph_json` and
  keeping the prep-joined list only for autopick's `data_type` reasoning
- Add a test: a workspace whose `chosen_artifact_ids` names an artifact absent from the prep-joined
  list must fail loudly rather than merging an autopicked substitute

### Related issues found in the same area

- `store/merge_crud.py :: update_merge_job_status` overwrites `error_message` and `result_path`
  unconditionally, including with `None`. Harmless under current call ordering; a future
  intermediate status omitting `result_path` would erase it
- `validate` calls `check_namespace_compatibility(..., explicit_only=True)` while `submit` calls it
  without the flag — a workspace can validate green and then be rejected at submit
- In `validate_merge_workspace`, a `get_biom_sample_ids` failure falls back to the study-level
  sample list, which is a **superset** of actual BIOM membership — silently overstating the merge
  overlap preview with no warning surfaced

### Files

- `qiita_explore/backend/helpers/merge_helpers.py` (`_resolve_artifact`, `_get_artifacts`)
- `qiita_explore/backend/routes/merge_routes.py` (`validate_merge_workspace`, `submit_merge_job`)
- `qiita_explore/backend/helpers/artifact_graph.py` (`fetch_artifact_graph`)
- `qiita_explore/backend/store/merge_crud.py` (`update_merge_job_status`)
- Documented in `docs/07-merge-and-biom.md` § "Two artifact lists, not one"

---

## TKT-045: `barnacle_backend_env.sh` Leaks Secrets Into Unpruned `.env.bak` Files

**Severity:** High
**Status:** Open

### Description

`Qiita/barnacle_backend_env.sh` writes a full timestamped backup of the backend `.env`
(`.env.bak.<timestamp>`) on every invocation, and nothing ever prunes them. Those backups contain
secrets in plaintext:

- `OPENAI_API_KEY` / `API_KEY` (the NRP-Nautilus LLM key)
- `QIITA_EXPLORE_PAT_ENCRYPTION_KEY` (the Fernet key protecting every stored Qiita PAT)

The Fernet key is the serious one. `helpers/pat_crypto.py` deliberately keeps that key **out of
SQLite** so a leaked database file or backup does not also leak long-lived Qiita bearer
credentials — its module docstring says exactly this. Scattering plaintext copies of the key across
timestamped files on the deployment host partly defeats that design: an attacker holding the SQLite
file plus any one `.env.bak.*` has both halves.

Note: `Qiita/barnacle_backend_env.sh` is tracked in git but currently **deleted from the working
tree** (` D` in `git status`). Read it with `git show HEAD:Qiita/barnacle_backend_env.sh`.

Found 2026-07-18 while documenting operations.

### Plan

- Decide first whether the script should be restored or is intentionally gone. If gone for good, the
  remaining task is cleaning up `.env.bak.*` files already on the barnacle host
- Stop taking full-file backups. The script's purpose is idempotently pinning a few keys
  (`QIITA_CONTROL_PLANE_URL`, `QIITA_PUBLIC_LOGIN_URL`, and deleting `QIITA_LOGINROCKET_URL`) — an
  in-place edit, or a backup of only the changed lines, is sufficient
- If a backup is genuinely wanted, keep exactly one (`.env.bak`, overwritten) and `chmod 600` it
- Audit the barnacle host for existing `.env.bak.*` files and remove them. Confirm `.gitignore`
  covers `.env.bak.*`, not just `.env`
- Consider rotating `QIITA_EXPLORE_PAT_ENCRYPTION_KEY` given unknown exposure duration. Rotation
  invalidates every stored PAT ciphertext; `store/auth_store.py` has no re-encryption path, and
  `helpers/auth_middleware.py` revokes sessions whose PAT fails to decrypt — so rotation degrades
  gracefully into "everyone logs in again" rather than breaking

### Files

- `Qiita/barnacle_backend_env.sh` (tracked, deleted from working tree)
- `qiita_explore/backend/helpers/pat_crypto.py` (the design this undermines)
- `.gitignore`
- Documented in `docs/09-operations.md` § routine maintenance

---

*Generated: 2026-05-19 | Updated: 2026-07-18*

---