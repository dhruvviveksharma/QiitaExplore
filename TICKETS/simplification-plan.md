# Simplification Plan (revised)

**Date:** 2026-07-05
**Goal:** Reduce dead code, reuse logic, apply simpler logic where applicable. Enforce the 500-line cap on all `qiita_explore/` files. **No code changes in this pass — planning only.**

## STATUS UPDATE (2026-07-14 execution pass)

S-1 through S-9 and S-15 are **DONE**. S-4 is partially done (see note). S-10 through S-13 and S-16 remain **PENDING** — deferred to keep this pass to dead-code/dedup, not structural file splits. See the updated Phase 3 table below for current line counts.

Additionally, out of the original S-list scope but done in the same pass:
- Store-layer `resolved_user = (user_id or "").strip() or "default"` (20 sites across `crud.py`, `cache.py`, `global_chat_crud.py`) consolidated into `store/db.py::_resolve_user()`.
- Dead migration removed: `store/db.py` `ALTER TABLE study_detail_cache ADD COLUMN samples_context` (already declared in the `CREATE TABLE`, guaranteed no-op).
- `helpers/merge_helpers.py` created (`_get_artifacts`, `_type_filtered_artifacts`, `_resolve_artifact`, `_get_sample_ids`) — completes the S-14 file-split's "extract shared merge helpers" half without doing the full route-file split.
- Frontend: `streamChat()` + `createProjChatAndSeed()`/`createGlobalChatAndSeed()` helpers extracted in `app_state.js`; dead `reportStudyId` param removed from `applyStreamDone`; `auth.js` now uses `apiPost` (drops manual CSRF header threading through `app.js` → `LegacyClaimBanner`).
- Repo hygiene: `ezredbiom/` leftovers deleted, tracked DB/log/tarball junk untracked + `.gitignore` updated, orphaned `wreath-loader.html` / `.claude/serve_frontend.py` deleted.

## STATUS UPDATE (2026-07-15 execution pass — "how much more can go")

A second pass, scoped to safe-mechanical dedup only (no structural component rewrites), plus two explicitly-authorized behavior changes. Verified with `bash run_tests.sh --unit` (95 passed) and a Flask route-registration check (53 routes, down from 54) after every phase, plus a Babel/browser load check for the frontend.

**Deleted outright (zero references, confirmed by repo-wide grep):**
- `qiita_explore/scripts/qiita_fastq_prep.py` (248 lines) — untouched since the `ezredbiom→qiita_explore` rename, QIIME2 prep isn't an app feature.
- Orphaned route `GET /api/projects/<project_id>/chats` (`routes/chat_routes.py`) — frontend only ever POSTs there; the chat list comes embedded in `GET /api/projects/<id>`. Kept `store/crud.list_chats` (used by `test_chats.py`).
- `qiita_explore/logs/` directory (nothing tracked, self-recreating).
- Unused deps `tinydb` and `qiita-files` from both `requirements.txt`/`requirements.prod.txt` (their only reason for being there — a stale "referenced by sql_store migration code" comment — was itself wrong); `pandas` dropped too since its only importer was the deleted `qiita_fastq_prep.py`.
- `TICKETS/done.md`, `TICKETS/staging.md`, `TASKS.md` — unfilled templates / stale premise (`test_data_studies/` doesn't exist).

**Backend dedup (safe-mechanical):**
- `helpers/sample_search.py` 274 → 240: extracted `_probe_pool`, `_probe_exists`, `_parallel_probe`, `_hydrate_headers` — the two search functions shared ~92 lines of pool/executor/timeout/hydration boilerplate around genuinely different SQL probes. Also dropped a pre-existing unused `import psycopg2`.
- `store/db.py`: the 6 hand-written `try: ALTER TABLE … except: pass` blocks folded into the `(table, col, definition)` loop that already existed for the `project_studies` columns.
- `services/study_service.py` 272 → 246: reused `qiita_fetch._row_to_study_header` for the row→dict mapping and hoisted the shared correlated-subquery column block (`num_samples`/`data_types`/`num_preps`) into `qiita_fetch._STUDY_COUNT_COLUMNS`, imported into both. The *different* visibility-filter WHERE logic was left untouched in each function — only the byte-identical parts moved.
- `helpers/agent_tools.py`: added `_result_studies()` (the duplicated `result_studies` list-shape) and `_empty_input_result()` (the duplicated "no search criteria" `ToolResult` shape).
- `store/merge_crud.py` 235 → 228: `_ws_studies()`/`_touch()` replace 4 copies of the re-select-studies query and 3 copies of the `updated_at` touch.
- 6 confirmed-unused imports removed (`cache.py`, `agent.py`, `qiita_fetch.py`, `merge_routes.py`, `biom_autopick.py`); 2 dead `config.py` constants removed (`anthropic_client` — an eagerly-constructed, never-read Anthropic client; `PROJECT_SUMMARY_GEN_LIMIT`); `_chat_title()` helper in `db.py` for the remaining title-normalization duplication.
- **Skipped as not worth it on closer inspection:** the `_str_list`/tool-dispatch-dict ideas from the initial sweep — the "5 identical" list-comprehensions actually have 3 different filter behaviors (some cast to `str()` before the truthiness check, one doesn't), so unifying them would silently change edge-case behavior on malformed LLM tool-call args; and the `llm_chat`/`llm_chat_stream` shared preamble is a net line *increase* once the helper's own def line is counted.

**Authorized behavior changes:**
- Removed the ~150-line dead TinyDB→SQLite migration path from `store/db.py` (465 → 288 lines after both changes): `_parse_tinydb_docs`, `_insert_project_doc`, `_insert_global_bucket`, `_migrate_from_tinydb`, `_should_migrate`, `_mark_migration`, plus the `TINYDB_*_PATH` constants. No `projects.json` exists anywhere; fresh-DB bootstrap and the *separate* `_reconcile_legacy_users_table` migration (a live, different migration for a stale pre-auth `users` table) were left untouched and both re-verified passing via `TestLegacyUsersMigration`.
- Removed the `compute_diversity` stub (schema, dispatch, `_tool_compute_diversity`, its `_tool_label` entry, its `GLOBAL_CHAT_SYSTEM_PROMPT` line) — it only ever returned "not yet available." TKT-010 updated to reflect the tool no longer exists (was a stub to finish; is now a from-scratch build).
- **Honest result:** `agent_tools.py` landed at **507 lines**, not fully under the 500 cap as hoped — the safe dedups within the file are exhausted; the remaining ~7 lines would need either a signature-mangling dispatch-table refactor (net quality loss — 3 of 4 tool functions would gain unused params) or the already-ticketed file split (TKT-013/TKT-039, left open).

**Frontend dedup (safe-mechanical):**
- `style.css` 1885 → 1815 (−70): removed 8 fully-dead rule blocks (`.typing-caret`+`@keyframes blink`, `.tool-result-table`, `.main-tabs`/`.main-tab*`, `.merge-artifacts-use-col`/`.merge-biom-check`, `.ctx-*` family, `.via-badge`, the old `<select>`-based merge-slot UI, `.merge-actions`), a byte-identical duplicate `.msg-bubble` rule, and 2 no-op `font-family: var(--undefined)` declarations. Every removed selector was grep-verified against all JS + `index.html` (substring match, so dynamic `className` construction was accounted for). Stopped short of the smaller comma-merge opportunities (2–9 lines each) — they require moving rules across large distances in the file, which is harder to visually re-verify without a live backend for pixel-level regression checking.
- `app_state.js` 633 → 611: added `patchChat()` (whole-entry sibling to `patchLast`, used by `unpinStudy`/`pinStudy`/`removeCtxStudyFromChat`), `chatScopeUrl()` (project-vs-global URL prefix, used by hydration/pin/unpin/stream), `ensureChatId()` (the 3 repeated create-chat-if-missing blocks in `sendMessage`), `dropChat()` (shared cache eviction); merged `openProjChat`/`openGlobChat` into one internal `openChat()` (external call signatures unchanged — no other files touched); `newProjChat`/`newGlobChat` now reuse `createProjChatAndSeed`/`createGlobalChatAndSeed`; removed 6 dead returned values (`loadProjects`/`fetchProjectDetail`/`loadGlobalChats`/`loadFirstStudies`/`lastContent`/`firstStudies` — none destructured outside the hook).
- `app_render.js`: removed dead `lastUiMsg` (zero references, also re-computed+reversed the message array every render); replaced a re-inlined copy of the `hasSourcesBar` expression with the variable; dropped the now-unreferenced `firstStudies` destructure entry.
- Cross-file: added `splitTypes()` to `utils.js` for the `(x.data_types||'').split(',')…` idiom repeated in `components.js`, `app_render.js`, `merge_artifacts.js`, `study_modal.js`; hoisted `_ICON_STYLE` in `icons.js` (4 of 5 icons shared the identical base style object); removed `SamplesBrowser`'s never-read `totalSamples` prop and both call sites.
- Verified via Babel/browser load (no console errors, correct render) — same sandbox limitation as Pass 1: no live backend, so only the anonymous auth-gate path was visually exercised; the chat/merge/search flows touched by `app_state.js` need a live-backend pass before merge.

**Ticket filed:** TKT-046 — the e2e test suite, `run_tests.sh`'s non-unit preflight, and all 4 benchmark scripts have been silently dormant since the auth middleware landed (401s with no session cookie, several swallowed into false-negative readings rather than failures). Found while auditing what still exercises the code touched here.

**Final line counts:** `agent_tools.py` 507, `sample_search.py` 240, `db.py` 288, `study_service.py` 246, `qiita_fetch.py` 491, `merge_crud.py` 228, `config.py` 198, `style.css` 1815, `app_state.js` 611, `app_render.js` 600, `components.js` 684 (untouched beyond the dead-prop removal — still over cap, split remains ticketed).

**Source documents**
- `TICKETS/tickets.md` — source of truth (TKT-002 .. TKT-040, reconciled 2026-06-21)
- `TICKETS/simplification-plan.md` (prior version, dated 2026-07-05) — **STALE**: uses old ticket IDs TKT-016..030 and contains one materially wrong recommendation (remove `_qiita_fetch`). This revision supersedes it.

---

## RESOLVE CONFLICTS

### TKT-026 (remove `_study_header_cache`) vs TKT-017 (promote to shared SQLite) — TKT-017 is correct; TKT-026 is wrong.

`_study_header_cache` / `_fetch_study_header_cached` (`qiita_fetch.py:266-285`) is the L1 TTL cache for **header fields** — `study_title`, `study_abstract`, `pi_name`, `pi_affiliation`, `data_types`, `num_samples`, `num_preps`. The SQLite `study_detail_cache` (`store/cache.py`, `get_study_detail_cache`) stores `preps_json`, `artifacts_json`, `samples_context`, `full_samples_json` — but **never the header fields**. Removing the in-process cache (TKT-026) would force a PostgreSQL round-trip for header data on every pinned-context load and every chat message.

**Decision:** Keep the L1 dict. Add write-through to a shared SQLite table (TKT-017). Close TKT-026 as Invalid (or supersede with TKT-017).

### TKT-025 (remove `_qiita_fetch`) — already Invalid. Confirmed used at 10 call sites.

`_qiita_fetch` (`qiita_fetch.py:119`) wraps `pooled_fetchall` with try/except and a default. Used at lines 130, 144, 150, 179, 206, 212, 232, 414, 464, 491. **Do not remove.**

---

## STALE PLAN ITEMS (prior `simplification-plan.md`)

| Prior plan item | Problem | Correct current ticket |
|---|---|---|
| TKT-016 "Remove `_qiita_fetch()`" | **WRONG** — function is used (10 sites). TKT-025 already marked Invalid. | — (do not action) |
| TKT-016 "Remove `_study_header_cache`" | Cache is needed (hot path for header fields). | TKT-017 (write-through), NOT TKT-026 |
| TKT-017 "Remove test.ipynb" | Correct in isolation; renumbered. | TKT-027 |
| TKT-018 "Clean up mid-module imports" | Correct; renumbered. | TKT-028 |
| TKT-019 "Consolidate study header queries" | Correct; renumbered. | TKT-029 |
| TKT-020 "Consolidate sample fetch functions" | Correct; renumbered. | TKT-030 |
| TKT-021 "Extract `request_utils.py`" | Correct; renumbered. | TKT-031 |
| TKT-022 "Consolidate SSE streaming patterns" | Correct; renumbered. | TKT-032 |
| TKT-023 "Extract `useModelSelection`" | Correct; renumbered. | TKT-033 |
| TKT-024 "Consolidate date formatting" | Correct; renumbered. | TKT-034 |
| TKT-025 "Simplify slash command matching" | Correct; renumbered. | TKT-035 |
| TKT-026..030 (file splits) | Renumbered. | TKT-036..040 |
| "Implementation Order" section | References stale TKT-016..030 IDs. | Use task IDs S-1..S-15 below |

---

## WHAT NOT TO DO

These look like simplification but would break behavior:

1. **Remove `_qiita_fetch()`** — used at 10 call sites (`qiita_fetch.py:130,144,150,179,206,212,232,414,464,491`). TKT-025 is Invalid.
2. **Remove `_study_header_cache` without TKT-017 write-through** — 3 hot-path call sites depend on it for header data; SQLite cache doesn't store header fields.
3. **Collapse to SQLite-only after TKT-017** — the in-process L1 is the fast path; SQLite is L2. Keep both layers.
4. **Remove `helpers/pg_pool.py` / `pooled_fetchall`** — foundation for all Qiita PostgreSQL queries; `_qiita_fetch` wraps it.
5. **Delete `helpers/llm_helpers.py`** — imported by `agent.py`, `chat_routes.py`, `global_chat_routes.py`, `qiita_fetch.py`. It is the context-building layer.

---

## PHASE 1 — DEAD CODE & PLACEMENT (Quick wins, no behavior change)

| ID | Title | Ticket | Files | Δ Lines | Agent | Risk | Status |
|----|-------|--------|-------|---------|-------|------|--------|
| **S-1** | Delete legacy `test.ipynb` | TKT-027 | `qiita_explore/test.ipynb` | -79 | Explore→SWE | Low | **DONE** |
| **S-2** | Move mid-module imports to top of `llm_helpers.py` | TKT-028 | `backend/helpers/llm_helpers.py:55-59` | 0 net | Explore→SWE | Low | **DONE** (also applied to `qiita_fetch.py`, which had the same issue) |

**Acceptance criteria**
- S-1: `ls qiita_explore/test.ipynb` → "No such file"; `git status` shows deletion only.
- S-2: `grep -n "^from store" llm_helpers.py` returns line < 10; `python -c "from helpers import llm_helpers"` succeeds; all referenced symbols (`get_project_context_summary`, `get_study_detail_cache`, `upsert_study_detail_cache`) remain used at lines 217, 224, 267.

---

## PHASE 2 — LOGIC CONSOLIDATION (Behavior-preserving refactors)

| ID | Title | Ticket | Files | Δ Lines | Agent | Depends | Risk | Status |
|----|-------|--------|-------|---------|-------|---------|------|--------|
| **S-3** | Consolidate duplicate study-header SELECTs | TKT-029 | `backend/helpers/qiita_fetch.py` | -40 | Explore→SWE→Tester | S-2 | Low | **DONE** |
| **S-4** | Consolidate sample fetch functions | TKT-030 | `backend/helpers/qiita_fetch.py` | -20 | Explore→SWE→Tester | S-3 | Low | **PARTIAL** — `_fetch_sample_context_text` delegates to `_fetch_full_sample_metadata`; `_fetch_study_samples` intentionally kept separate (different return shape: 3 named JSON keys vs full `sample_values`). Won't-merge. |
| **S-5** | Extract shared `request_utils.py` | TKT-031 | new `backend/helpers/request_utils.py`; `routes/chat_routes.py`, `routes/global_chat_routes.py` | -30 | Explore→SWE→Reviewer | S-2 | Medium | **DONE** for chat/global-chat routes (`parse_chat_stream_body`, `build_full_msgs`, `sse_response`, `stream_samples_report`). `project_routes.py`/`merge_routes.py` didn't have the same duplicated shape — not touched. |
| **S-6** | Extract `useModelSelection()` hook | TKT-033 | `frontend/js/app_state.js` (-25); new `frontend/js/hooks/useModelSelection.js` | -20 net | Explore→SWE→Tester | — | Low | **DONE** |
| **S-7** | Consolidate date formatting | TKT-034 | `frontend/js/utils.js` (+8); `frontend/js/app_render.js` (-5 at lines 96, 161) | -5 net | SWE→Tester | — | Low | **DONE** |
| **S-8** | Simplify slash command matching | TKT-035 | `frontend/js/app_state.js:628-632` | -5 | SWE→Tester | — | Low | **DONE** |
| **S-9** | Write-through `_study_header_cache` → SQLite | TKT-017 | `backend/store/db.py` (migration), `store/cache.py`, `helpers/qiita_fetch.py` | -15 | Explore→SWE→Reviewer→Tester | S-3 | Medium | **PENDING** |

**Acceptance criteria**
- S-3: `grep -c "SELECT s.study_id" qiita_fetch.py` returns 1 (single shared `_build_study_header_query`); integration test diffs `first_studies()` vs `_fetch_study_header()` results on 3 study IDs and shows identical columns.
- S-4: `_fetch_study_samples` and `_fetch_full_sample_metadata` merged into one with optional `limit` param; test confirms LIMIT behavior and no change to returned shape.
- S-5: all 4 routes import `get_user_id`, `parse_study_ids`, `_sse` from `request_utils.py`; `python -c "from routes import chat_routes, global_chat_routes, project_routes, merge_routes"` succeeds; duplicate `_sse` definitions removed.
- S-6: `grep -l "useModelSelection" frontend/js/` finds new hook file and `app_state.js`; manual test: select model, reload, model persists; switch chats, each remembers its model.
- S-7: `grep "toLocaleDateString" app_render.js` returns 0; `formatDate` in `utils.js`; visual diff of chat list dates shows identical output.
- S-8: `useMemo` removed; `slashMatches` is inline conditional; manual test: typing `/` shows slash command list, typing `/pin` filters to pin command.
- S-9: schema migration adds `header_json` column to `study_detail_cache` (or new table); `_fetch_study_header_cached` writes through to SQLite; integration test: kill worker, restart, header served from SQLite L2 without PostgreSQL hit.

---

## PHASE 3 — FILE SPLITS (Mechanical, enforce 500-line cap)

**Current line counts** (`wc -l`, verified 2026-07-14, after Phase 1/2 work above):

| File | Lines | Over cap | Tracked by |
|------|-------|----------|------------|
| `frontend/js/components.js` | 684 | +184 | TKT-038 |
| `frontend/js/app_state.js` | 633 | +133 | TKT-036 |
| `frontend/js/app_render.js` | 601 | +101 | TKT-037 |
| `backend/helpers/agent_tools.py` | 548 | +48 | TKT-013 / TKT-039 |
| `backend/helpers/qiita_fetch.py` | 486 | — | TKT-040 (closed — under cap) |
| `backend/routes/merge_routes.py` | 364 | — | TKT-014 (closed — under cap after S-14's helper extraction) |
| `backend/store/crud.py` | 412 | — | TKT-011 (closed — under cap, see S-15) |

Already resolved:
- `qiita_fetch.py`: 534 → 486 (S-3 + S-4) — **under cap, split (S-16) not needed.**
- `merge_routes.py`: 528 → 364 — the shared artifact/sample-resolution helpers (`_get_artifacts`, `_type_filtered_artifacts`, `_resolve_artifact`, `_get_sample_ids`) moved to new `helpers/merge_helpers.py` (48 lines), also fixing the route→route import from `artifact_routes.py`. **The route-file split itself (new `merge_workspace_routes.py`/`merge_job_routes.py`) is no longer needed** — under cap without it.
- `crud.py`: 515 → 412 (S-15, `global_chat_crud.py` extracted) — **under cap.**

Still over cap, split still required:
- `app_state.js`: 672 → 633 (S-6, S-8, plus new `streamChat`/`createProjChatAndSeed`/`createGlobalChatAndSeed` helpers) — still over, split (S-10) required.
- `app_render.js`: 601 — unchanged, split (S-11) required.
- `components.js`: 688 → 684 — still over, split (S-12) required.
- `agent_tools.py`: 548 — unchanged, split (S-13) required.

| ID | Title | Ticket | Files | Δ Lines | Agent | Depends | Risk | Status |
|----|-------|--------|-------|---------|-------|---------|------|--------|
| **S-10** | Split `app_state.js` (633, still over) | TKT-036 | `frontend/js/app_state.js`; new `frontend/js/sse_helpers.js`, `frontend/js/chat_actions.js`, `frontend/js/search_helpers.js` | 0 net | SWE→Tester | S-6, S-8 | High | **PENDING** |
| **S-11** | Split `app_render.js` (601→~596) | TKT-037 | `frontend/js/app_render.js`; new `sidebar_renderer.js`, `browse_renderer.js`, `chat_renderer.js`, `composer_renderer.js` | 0 net | SWE→Tester | S-10 | High | **PENDING** |
| **S-12** | Split `components.js` (684) | TKT-038 | `frontend/js/components.js`; new `model_picker.js`, `pinned_bar.js`, `slash_commands.js` | 0 net | SWE→Tester | S-11 | High | **PENDING** |
| **S-13** | Split `agent_tools.py` (548) | TKT-013 | `backend/helpers/agent_tools.py`; new `agent_tool_schemas.py`, `agent_tool_executors.py` | 0 net | Explore→SWE→Reviewer→Tester | S-5 | Medium | **PENDING** |
| **S-14** | Split `merge_routes.py` (528) | TKT-014 | `backend/routes/merge_routes.py`; new `merge_workspace_routes.py`, `merge_job_routes.py`, `helpers/merge_helpers.py` | 0 net | SWE→Tester | S-5 | Medium | **DONE (helper half only)** — `helpers/merge_helpers.py` extracted (also used by `artifact_routes.py`, fixing a route→route import); file dropped to 364 lines so the route-file split is no longer needed. |
| **S-15** | Split `crud.py` (515) | TKT-011 | `backend/store/crud.py`; new `crud_projects.py`, `crud_chats.py`, `crud_search.py` | 0 net | SWE→Tester | — | Medium | **DONE** — via `global_chat_crud.py` extraction (different split shape than originally proposed, same outcome: under cap). |
| **S-16** | Split `qiita_fetch.py` (534, optional after S-3/S-4) | TKT-040 | `backend/helpers/qiita_fetch.py`; new `qiita_queries.py`, `qiita_study_context.py` | 0 net | SWE→Reviewer→Tester | S-3, S-4 | High | **CLOSED — not needed** (486 lines, under cap) |

**Acceptance criteria (all splits)**
- `wc -l <file>` ≤ 500 after split.
- All new files import correctly: `python -c "from helpers import qiita_fetch, agent_tools"` succeeds; `node -e "require('./frontend/js/components.js')"` or browser smoke test passes.
- No behavior change: existing pytest suite passes; manual smoke test of affected flows (search, chat, merge, pin) passes.
- Import audit: Reviewer confirms no orphaned imports or broken re-export chains.

---

## DEPENDENCY GRAPH

```
Phase 1                          Phase 2                           Phase 3
───────                          ───────                           ───────
S-1 (delete ipynb) ─────────────► (independent)
S-2 (move imports) ─┬─► S-3 (header queries) ─► S-4 (sample fetch) ─► S-16 (qiita_fetch split, optional)
                    │            │
                    │            └─► S-9 (write-through cache) [needs schema migration]
                    │
                    ├─► S-5 (request_utils) ─┬─► S-13 (agent_tools split)
                    │                        └─► S-14 (merge_routes split)
                    │
                    ├─► S-6 (useModelSelection) ─► S-10 (app_state split) ─► S-11 (app_render split) ─► S-12 (components split)
                    │
                    ├─► S-7 (date formatting) ─► S-11
                    │
                    └─► S-8 (slash matching) ─► S-10

S-15 (crud.py split) — independent, run anytime in Phase 3
```

**Critical path:** S-2 → S-3 → S-4 → S-16 (qiita_fetch consolidation then optional split) and S-6 → S-10 → S-11 → S-12 (frontend hook extraction then sequential splits).

---

## TOTAL ESTIMATED LINE REDUCTION

| Phase | Change | Est. Lines |
|-------|--------|-----------|
| Phase 1 (dead code + placement) | Delete test.ipynb, move imports | -79 |
| Phase 2 (logic consolidation) | Query dedup, extract hooks, inline simple code | -100 |
| Phase 3 (file splits) | Zero net lines — redistribution only | 0 net |
| **Total direct reduction** | | **~179 lines** |

**File line counts after completion:**

| File | Before | After Phase 2 | After Phase 3 (split) |
|------|--------|---------------|----------------------|
| `components.js` | 688 | 688 | ≤500 |
| `app_state.js` | 672 | ~647 | ≤500 |
| `app_render.js` | 601 | ~596 | ≤500 |
| `agent_tools.py` | 548 | 548 | ≤500 |
| `qiita_fetch.py` | 534 | ~474 | ≤500 (or skip split) |
| `merge_routes.py` | 528 | 528 | ≤500 |
| `crud.py` | 515 | 515 | ≤500 |

---

## RISKS & MITIGATIONS

| Task | Risk | Mitigation |
|------|------|-----------|
| S-3 (header query consolidation) | Merging `first_studies()` and `_fetch_study_header()` may subtly change columns returned for initial browse | Add integration test that calls both before and after, diffs results on at least 3 study IDs |
| S-5 (request_utils extraction) | Changing import paths in 4 routes risks import errors on startup | Update imports and test startup (`python -c "from routes import chat_routes"`) before committing |
| S-9 (write-through cache) | Schema migration on `study_detail_cache` must be backwards-compatible | Add `header_json` as nullable column; write-through is additive; old workers still serve from L1 dict |
| S-10 (app_state.js split) | Closure over shared state is fragile; splitting action handlers may break `useAppState()` contract | Run full interaction test: create project, send message, pin study, delete chat — all must work |
| S-12 (agent_tools.py split) | `execute_tool` is called from `agent.py` and `_execute_tool_call`; changing import shape breaks runtime | Extract to new files first, then re-export from `agent_tools.py`, then migrate callers in follow-up PR |
| S-13 (qiita_fetch.py split) | Most critical split — 10+ call sites across routes, services, and `llm_helpers` | Keep `qiita_fetch.py` as re-export layer with `from helpers.qiita_queries import *` to minimize caller changes |

---

## AGENT ASSIGNMENTS

Per goal: all agents run on **sonnet** or **minimax-m2**.

| Agent | Role | Tasks |
|-------|------|-------|
| **Explore** | Verify scope, enumerate duplications, confirm dead code | S-1 (verify), S-2 (verify), S-3 (verify query identity), S-4 (verify param convergence), S-5 (enumerate duplications), S-9 (verify cache layering), S-13 (verify tool callers) |
| **Plan** | Produce task spec, resolve conflicts, map to tickets | (already done — this document) |
| **SWE** | Implement minimal code changes, output unified diff | All S-* implementation steps |
| **Reviewer** | Spec traceability, correctness review, import audit | S-5, S-9, S-13, S-16 (high-risk refactors) |
| **Tester** | Derive test cases from AC, run pytest + manual smoke tests | All S-* — verifies each AC is met |
| **agent-tooling** | SSE/agent.py streaming duplication verification (TKT-032) | Verify `_stream_anthropic_agent` vs OpenAI path duplication; confirm SSE event contract intact |

---

## NOTES ON THE PRIOR PLAN'S CLAIMS

The prior `simplification-plan.md` (2026-07-05) made several claims that this revision verified or corrected:

1. **"Remove `_qiita_fetch()`"** — **WRONG**. Function is used at 10 call sites. TKT-025 is marked Invalid in `tickets.md`. The prior plan's "Impact: ~23 lines removed" would have broken the entire Qiita fetch layer.

2. **"Remove `_study_header_cache`"** — **PARTIALLY WRONG**. The cache is needed (hot path for header fields that SQLite `study_detail_cache` doesn't store). The correct fix is TKT-017 (write-through to SQLite), not removal. TKT-026 (remove) should be closed as Invalid.

3. **"Consolidate duplicate study header queries"** — **CORRECT**. `first_studies()` (lines 51-116) and `_fetch_study_header()` (lines 411-459) share identical SELECT structure. Maps to TKT-029.

4. **"Extract shared `request_utils.py`"** — **CORRECT**. `chat_routes.py` (221 lines) and `global_chat_routes.py` (307 lines) duplicate `_sse`, user_id extraction, pin handling. Maps to TKT-031.

5. **"Consolidate date formatting"** — **CORRECT**. `app_render.js` uses `toLocaleDateString('en-US', {month:'short', day:'numeric'})` at lines 96 and 161. Maps to TKT-034.

6. **"Simplify slash command matching"** — **CORRECT**. `app_state.js:628-632` uses `useMemo` for slash matching; can be inlined. Maps to TKT-035.

7. **File split estimates** — **MOSTLY CORRECT**, with one correction: `qiita_fetch.py` (534 lines) drops to ~474 after S-3 + S-4 (query + sample fetch consolidation), so it falls **under the 500-line cap** and the split (S-16) becomes **optional**.

---

## SUCCESS CRITERIA

- [ ] No file in `qiita_explore/` exceeds 500 lines — **not yet**: `components.js` (684), `app_state.js` (633), `app_render.js` (601), `agent_tools.py` (548) still over; S-10..S-13 pending.
- [x] `_qiita_fetch` preserved (grep confirms call sites).
- [ ] `_study_header_cache` write-through to SQLite (S-9) — pending; L1 dict retained.
- [x] No duplicated study-header SELECTs (single `_build_study_header_query`).
- [x] `request_utils.py` shared across `chat_routes`, `global_chat_routes` (not `project_routes`/`merge_routes` — no matching duplication found there).
- [x] `formatDate` in `utils.js`; `toLocaleDateString` removed from `app_render.js`.
- [x] Slash matching inlined (no `useMemo`).
- [x] `test.ipynb` deleted.
- [x] Mid-module imports in `llm_helpers.py` and `qiita_fetch.py` moved to top.
- [x] All existing pytest unit tests pass (95 passed, `run_tests.sh --unit`) after this pass.
- [ ] Manual smoke test: search, global chat (tool-capable model), project chat, pin study, merge workflow, study modal — **not run**; this sandbox has no live Postgres/Qiita DB/conda env to start the real backend. Frontend-only smoke test (page load, no console errors, anonymous-auth gate renders) passed; full authenticated golden path needs to be verified against a live backend before merge.
