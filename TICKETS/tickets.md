# Active Tickets

> Known bugs and issues requiring attention.

---

## TKT-001: Debug Port Not Reverted Before Merge

**Severity:** Critical
**Status:** Resolved

### Description

Several files were set to port 5002 for debug/testing. Master and barnacle production use port 5001 (Gunicorn bind, nginx upstream, frontend `api-base`). Mismatch breaks API calls and nginx proxying.

### Resolution

All ports reverted to 5001 across `start_barnacle.sh`, `run.py`, `utils.js`, `run_tests.sh`, `tests/e2e/conftest.py`; `index.html` and `nginx.conf` confirmed already 5001. Smoke test: `curl http://localhost:5001/api/health`.

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

| File | Lines | Tracked by |
|------|-------|-----------|
| `frontend/js/components.js` | 630 | TKT-011 |
| `frontend/js/app_state.js` | 626 | TKT-011 |
| `frontend/js/app_render.js` | 570 | TKT-011 |
| `backend/helpers/agent_tools.py` | 548 | TKT-013 |
| `backend/helpers/qiita_fetch.py` | 532 | TKT-011 |
| `backend/routes/merge_routes.py` | 528 | TKT-014 |
| `backend/store/crud.py` | 515 | TKT-011 |

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

1. **`MergesTab` mount** (`frontend/js/merge_workspace.js`): on load, fetches full workspace detail for every workspace in parallel via `Promise.all(list.map(...))`.
2. **`GlobalBiomSelector` Smart Select** (`frontend/js/merge_artifacts.js`, `handleApply`): fetches all missing study details in parallel (up to 5), no rate limiting / cancellation.

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

*Generated: 2026-05-19 | Updated: 2026-06-21*
