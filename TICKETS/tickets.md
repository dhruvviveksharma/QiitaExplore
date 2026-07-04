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

- `ezredbiom/backend/routes/project_routes.py:42` — study-detail fetch/cache failure: `except Exception: pass` (user-facing study-add path)
- `ezredbiom/backend/routes/project_routes.py:69` — sample-context fetch failure: `except Exception: pass`
- `ezredbiom/backend/store/db.py:45` — bucket parse failure: silent `return []`
- `ezredbiom/backend/store/db.py:202,207,212,217,222,228` — migration / ALTER TABLE failures swallowed
- `ezredbiom/backend/store/cache.py:107` — cache parse failure swallowed
- `ezredbiom/backend/store/crud.py:71` — silent swallow
- `ezredbiom/backend/store/merge_crud.py:22` — silent swallow

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

`preps = []` is now initialized before the try block at `ezredbiom/backend/routes/project_routes.py:34`, and `artifacts` is only used inside the same try (for caching at :41), never downstream. Downstream guards on `if preps:` (:48). NameError risk eliminated. The remaining silent `except Exception: pass` at :42 is tracked under TKT-002.

---

## TKT-004: Race Condition in SSE Response (pin after "done")

**Severity:** Low
**Status:** Open

### Description

In the SSE handlers for global and project chat, if `pin_study_to_chat` fails *after* the SSE "done" message is already yielded, the error only logs and the user sees incorrect pinned-study state.

### Affected Files

- `ezredbiom/backend/routes/global_chat_routes.py` (SSE done handler)
- `ezredbiom/backend/routes/chat_routes.py` (SSE done handler)

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

- Composer is enabled on browse: `canSend = (isChat || view.type === 'browse') && input.trim().length > 0 && !sending` (`ezredbiom/frontend/js/app_state.js:581`); composer no longer muted on browse (`ezredbiom/frontend/js/app_render.js:523`)
- Enter from browse creates/sends into a global chat: browse branch in `sendMessage` (`ezredbiom/frontend/js/app_state.js:380`)
- Pinning supported via `/pin <ids>` command → `pin_study_ids` payload (`app_state.js:367`)

### Caveat

The "pin directly from a study card / context chip" UX (vs. the `/pin` command) was not separately verified. If that interaction is still desired, open a focused follow-up ticket; otherwise close fully.

---

## TKT-007: Refactor Away from qiita_db.TRN / qiita_core (then delete both packages)

**Severity:** Low
**Status:** Open

### Description

The live app depends on two files outside `ezredbiom/`:

- `qiita_db/sql_connection.py` — the `TRN` PostgreSQL transaction context manager
- `qiita_core/configuration_manager.py` — pulled in transitively by `sql_connection.py`

Everything else in `qiita_db/` (~15 MB, 80+ modules) and `qiita_core/` is dead weight in the repo.

### Affected Files (verified 2026-06-21 — 4 importers, not 3)

- `ezredbiom/backend/routes/study_routes.py`
- `ezredbiom/backend/helpers/qiita_fetch.py`
- `ezredbiom/backend/helpers/sample_search.py`
- `ezredbiom/backend/services/study_service.py`

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

The chat advertises a `compute_diversity` tool, but it is a hard stub — `ezredbiom/backend/helpers/agent_tools.py:538` returns *"Diversity analysis is not yet available."* This is the analysis payoff of the merge workflow: a user can merge BIOMs but cannot then analyze the result.

### Plan

- Ingest BIOM/OTU tables from a merge result bundle (merge already emits `result.tar.gz`; see `helpers/merge_executor.py`)
- Implement `_tool_compute_diversity` to compute alpha/beta metrics: Shannon, Faith's PD, Bray-Curtis, UniFrac
- Emit a `segment_tool_result` with a renderable `ui_payload` (new branch in `ToolResultWidget`, frontend `components.js`)
- Coordinate with TKT-015 — diversity is only meaningful once the merge execution path is trustworthy

### Files Changed

- `ezredbiom/backend/helpers/agent_tools.py`
- `ezredbiom/frontend/js/components.js`

---

## TKT-011: Split Oversized Files (500-line cap)

**Severity:** Low
**Status:** Open

### Description

Files over the 500-line `ezredbiom/` cap (verified 2026-06-21):


| File                             | Lines | Tracked by |
| -------------------------------- | ----- | ---------- |
| `frontend/js/components.js`      | 630   | TKT-011    |
| `frontend/js/app_state.js`       | 626   | TKT-011    |
| `frontend/js/app_render.js`      | 570   | TKT-011    |
| `backend/helpers/agent_tools.py` | 548   | TKT-013    |
| `backend/helpers/qiita_fetch.py` | 532   | TKT-011    |
| `backend/routes/merge_routes.py` | 528   | TKT-014    |
| `backend/store/crud.py`          | 515   | TKT-011    |


### Plan (TKT-011 scope)

**qiita_fetch.py** — extract sample fetching/caching into `helpers/qiita_samples.py` (`_fetch_full_sample_metadata`, `_get_or_fetch_full_samples`, `_fetch_sample_context_text`, `_build_full_samples_block`, `_build_samples_report_payload`, `_build_pinned_reports_context`); keep header/search/detect helpers in `qiita_fetch.py`.

**app_state.js** — extract action handlers into `app_actions.js` (sendMessage, unpinStudy, enrichAllStudies, doSearch, modal helpers, chat navigation); keep useState/derived/returned shape in `app_state.js`.

**components.js / app_render.js / crud.js** — split along feature boundaries (TBD per file).

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

`ezredbiom/backend/helpers/agent_tools.py` is 548 lines (verified 2026-06-21), over the cap.

### Plan

Extract tool implementations into `helpers/agent_tool_impls.py`:

- Move `_tool_search_studies`, `_tool_search_by_sample`, `_tool_get_study_report`, `_tool_pin_study`, `_tool_compute_diversity`
- Keep `TOOL_SCHEMAS`, `ToolResult`, `execute_tool`, and small helpers in `agent_tools.py`

---

## TKT-014: Split merge_routes.py (over 500-line cap)

**Severity:** Low
**Status:** Open

### Description

`ezredbiom/backend/routes/merge_routes.py` is 528 lines (verified 2026-06-21), over the cap.

### Plan

- `routes/merge_workspace_routes.py` — workspace CRUD + validate + samples
- `routes/merge_job_routes.py` — job submit, poll, download
- Shared helpers (`_get_artifacts`, `_type_filtered_artifacts`, `_user_id`) → `helpers/merge_helpers.py`

---

## TKT-015: Merge Executor Shipped in Dev-Only Mode

**Severity:** High
**Status:** Open

### Description

`ezredbiom/backend/helpers/merge_executor.py:3` carries a `TODO (before merging to master): replace _run_local with SFTP+SSH pipeline` — but the multi-LLM branch was merged to master with the local path still in place. The executor runs `conda run -n qiita python scripts/remote_merge.py` as a local subprocess (`merge_executor.py:100`), which only works if the serving host has the `qiita` conda env, the script, and local access to the BIOM files. On a real/remote deployment the merge job will fail.

### Plan

Choose one:

- **Finish the remote pipeline** — implement the paramiko SFTP+SSH path described in the TODO (upload BIOMs → ssh exec → download `result.tar.gz`); keep the same `on_status` interface.
- **Or gate/document local-only mode** — explicitly require the local `qiita` env and surface a clear error when prerequisites are missing, so master doesn't silently fail.

### Files

- `ezredbiom/backend/helpers/merge_executor.py`

---

*Generated: 2026-05-19 | Updated: 2026-06-24*

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

- `ezredbiom/backend/helpers/sample_search.py:165`, `:200`, `:229`
- `ezredbiom/backend/helpers/qiita_fetch.py:359`

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

- `ezredbiom/backend/helpers/qiita_fetch.py:270–285`
- `ezredbiom/backend/store/db.py` (migration), `store/cache.py`

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

- `ezredbiom/backend/helpers/sample_search.py:174–205`, `:239–260`
- `ezredbiom/backend/helpers/agent.py` (`_execute_tool_call`)
- `ezredbiom/frontend/js/components.js` (`ToolResultWidget`)

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

- `ezredbiom/backend/helpers/agent.py:120–153`, `:275–317`

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

- `ezredbiom/frontend/index.html`
- New: `ezredbiom/frontend/build.sh`

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

- `ezredbiom/backend/routes/` (new `cohort_routes.py`)
- `ezredbiom/backend/helpers/agent_tools.py` (new tool)
- `ezredbiom/frontend/js/components.js` (`CohortBubble`)

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

- `ezredbiom/backend/helpers/agent_tools.py`
- `ezredbiom/backend/helpers/sample_search.py` or new `helpers/cohort.py`
- `ezredbiom/frontend/js/components.js` (`FieldOverlapMatrix`)

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

- `ezredbiom/backend/helpers/biom_autopick.py`
- `ezredbiom/backend/helpers/qiita_fetch.py` (`_fetch_study_detail_from_qiita`)

