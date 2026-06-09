# Active Tickets

> Known bugs and issues requiring attention.

---

## TKT-001: Debug Port Not Reverted Before Merge

**Severity:** Critical
**Status:** Resolved

### Description

Several files were set to port 5002 for debug/testing. Master and barnacle production use port 5001 (Gunicorn bind, nginx upstream, frontend `api-base`). Mismatch breaks API calls and nginx proxying.

### Affected Files

- `ezredbiom/start_barnacle.sh` — Gunicorn bind port
- `ezredbiom/backend/run.py` — `app.run()` port (direct run only)
- `ezredbiom/frontend/js/utils.js` — API fallback when `meta api-base` missing
- `ezredbiom/backend/run_tests.sh` — default `BARNACLE_URL`
- `ezredbiom/backend/tests/e2e/conftest.py` — default `BARNACLE_URL`

### Already correct (no change)

- `ezredbiom/frontend/index.html` — `api-base` = `http://localhost:5001/api`
- `ezredbiom/nginx.conf` — upstream `127.0.0.1:5001`

### Plan

- `start_barnacle.sh` → 5001
- `run.py` → 5001
- `utils.js` fallback → 5001
- Confirm `index.html` stays 5001
- Confirm `nginx.conf` stays 5001
- Update test defaults to 5001
- Update INSTALL.md + CLAUDE.md port references
- Smoke test: `curl http://localhost:5001/api/health` with barnacle running

### Files Changed

- `ezredbiom/start_barnacle.sh`
- `ezredbiom/backend/run.py`
- `ezredbiom/frontend/js/utils.js`
- `ezredbiom/backend/run_tests.sh`
- `ezredbiom/backend/tests/e2e/conftest.py`
- `INSTALL.md`
- `CLAUDE.md`

---

## TKT-002: Silent Exception Handling Makes Debugging Difficult

**Severity:** Medium
**Status:** Open

### Description

15+ locations use `except Exception: pass` without any logging or user feedback. When failures occur, debugging is extremely difficult as errors are swallowed silently.

### Affected Files

- `sql_store_crud.py:56` - JSON decode failure returns None silently
- `sql_store_db.py:44` - TinyDB import errors ignored
- `sql_store_db.py:157,162,167,173` - Migration ALTER TABLE failures ignored
- `sql_store_cache.py:115` - Cache TTL parsing failure silently continues
- `routes/project_routes.py:50,77,168` - Qiita fetch and context failures ignored
- `helpers/qiita_fetch.py:252,258` - Samples fetch/cache failures ignored
- `helpers/llm_helpers.py:108` - Unknown location

### Plan

- Add `logger.exception()` or `logger.warning()` to all bare `except: pass` blocks
- For critical paths (Qiita fetches, study detail): return error response or set fallback values
- Consider adding a wrapper decorator for API calls that handles common error patterns
- Prioritize `project_routes.py` first as it handles user-facing operations

### Files Changed

- `ezredbiom/Experiment/backend/sql_store_crud.py`
- `ezredbiom/Experiment/backend/sql_store_db.py`
- `ezredbiom/Experiment/backend/sql_store_cache.py`
- `ezredbiom/Experiment/backend/routes/project_routes.py`
- `ezredbiom/Experiment/backend/helpers/qiita_fetch.py`
- `ezredbiom/Experiment/backend/helpers/llm_helpers.py`

### Files Added

- `ezredbiom/Experiment/backend/helpers/api_error_handler.py` (optional - for common patterns)

---

## TKT-003: Undefined Variables on Qiita Fetch Failure

**Severity:** Medium
**Status:** Open

### Description

In `project_routes.py:47-51`, if Qiita fetch fails during study add, the function continues without `preps`/`artifacts` being set. This can cause `NameError` when these variables are used downstream.

### Affected File

- `ezredbiom/Experiment/backend/routes/project_routes.py:47-51`

### Plan

- Add initialization of `preps = None`, `artifacts = None` before the try block
- Add null checks before using these variables
- Log the failure with `logger.warning()` so it's visible in logs
- Consider returning early with error response or using empty defaults

### Files Changed

- `ezredbiom/Experiment/backend/routes/project_routes.py`

---

## TKT-004: Race Condition in SSE Response

**Severity:** Low
**Status:** Open

### Description

In `global_chat_routes.py:159` and `chat_routes.py:172`, if `pin_study_to_chat` fails after the SSE "done" message is already yielded, the error only logs but the user sees incorrect pinned study state.

### Affected Files

- `routes/global_chat_routes.py:159`
- `routes/chat_routes.py:172`

### Plan

- Move the pin operation before yielding the SSE "done" message
- Or wrap in a transaction-like pattern: do work first, then respond
- Add `logger.error()` if pin fails after response is sent, for monitoring
- Consider implementing a retry mechanism or client-side reconciliation

### Files Changed

- `ezredbiom/Experiment/backend/routes/global_chat_routes.py`
- `ezredbiom/Experiment/backend/routes/chat_routes.py`

---

## TKT-005: LLM Chat Loses Conversation Context

**Severity:** Medium
**Status:** Open

### Description

The model does not retain context from previous questions in a chat session. For example, user filters for "wild mouse studies", then follows up with "shotgun studies" - but the model forgets the "mouse" filter and returns all shotgun studies instead of just shotgun studies from mice.

This appears to be a conversation history/context window issue, potentially related to Gemma architecture limitations.

### Affected Files

- Likely: `ezredbiom/Experiment/backend/helpers/llm_helpers.py` (message history handling)
- Likely: `ezredbiom/Experiment/backend/routes/chat_routes.py` or `global_chat_routes.py` (conversation context)

### Plan

- Investigate how message history is passed to the LLM (check if full chat history is included)
- Verify the conversation context is being appended correctly to each API call
- Check if there's a message limit causing older messages to be dropped
- Consider implementing a system prompt that explicitly instructs the model to consider prior conversation context
- Test with Gemma to determine if this is a model limitation or implementation bug
- If Gemma limitation: document as known issue, potentially switch to a model with better context retention

### Files Changed

- `ezredbiom/Experiment/backend/helpers/llm_helpers.py` (potentially)
- `ezredbiom/Experiment/backend/routes/chat_routes.py` (potentially)
- `ezredbiom/Experiment/backend/routes/global_chat_routes.py` (potentially)

---

## TKT-006: Pin Studies in Chat Bar + Enter to Start Global Chat

**Severity:** Medium
**Status:** Open

### Description

Users on the **Browse Studies** home view cannot use the bottom chat bar meaningfully today: the composer is muted, Enter does nothing, and pinned studies only appear after opening a global chat via the sidebar. This ticket adds two related flows:

1. **Pin studies in the chat bar (browse + composer)**
  - Show studies the user has added as context (`ctxStudies`) and/or explicitly pinned in the **composer area** on the home page (not only in the separate `ctx-bar` above the grid).
  - Allow pinning from browse (study cards, context chips, or composer chips) so selections are visible in the chat bar before a chat exists.
  - On first message, carry those studies into the new global chat (as `selected_studies` and/or persisted pins).
2. **Enter → new global chat**
  - From browse, when the composer has a non-empty message, **Enter** (without Shift) creates a new global chat and sends the message — same outcome as sidebar "+ New Global Chat" + typing + send, in one step.
  - Reuse existing lazy-create logic in `sendMessage` (`app_state.js` ~366–376) rather than inventing a parallel path.

### Current Behavior


| Concept                          | State                                  | Visible on browse                 | Persists       |
| -------------------------------- | -------------------------------------- | --------------------------------- | -------------- |
| **Context chips** (`ctxStudies`) | React session state                    | Yes — `ctx-bar` above grid        | No             |
| **Pinned studies**               | `chatCache[chatId].pinnedStudies` + DB | No — only in active chat composer | Yes (per chat) |


Pins today are only created via `/report <study_id>`; there is no `POST` pin endpoint (only `DELETE` unpin). The browse composer is disabled (`disabled={!isChat}`) and Enter is gated on `isChat`.

### UX Notes

- **Context vs pin:** `+ Context` toggles ephemeral `ctxStudies`; DB pins only happen via `/report`. Decide whether browse-bar chips stay as context until send (then optionally persist as pins) or add an explicit pin action separate from context.
- **Composer on browse:** Either enable the textarea when `view.type === 'browse'` or add `sendFromBrowse()` that switches to `global-chat` then calls `sendMessage`.
- **Placeholder:** Change from "Open a chat to start messaging" to something like "Ask about studies… (Enter to start chat)" on browse.
- **Cap:** Respect `PINNED_STUDIES_PER_CHAT_CAP` (10) when persisting pins.

### Affected Files

- `ezredbiom/Experiment/frontend/js/app_render.js` — composer pins row on browse, Enter handler, placeholder
- `ezredbiom/Experiment/frontend/js/app_state.js` — `canSend` / `isChat` rules, `pinStudy()`, browse→global-chat branch
- `ezredbiom/Experiment/frontend/style.css` — composer pin chips on muted/browse state (if needed)
- `ezredbiom/Experiment/backend/routes/global_chat_routes.py` — `POST /api/global-chats/<chat_id>/pinned/<study_id>` (mirror existing DELETE)
- `ezredbiom/Experiment/backend/sql_store_cache.py` — reuse `pin_study_to_chat`

### Plan

**Pin studies in chat bar**

- Render composer pin/context chips on `browse` view (reuse `ctxStudies` and/or draft pin list)
- Add pin action on study cards or context chips (not only `/report`)
- Add `POST .../pinned/<study_id>` for global chats; add `pinStudy(chatId, studyId)` in frontend (mirror `unpinStudy`)
- Show study titles in pin chips (not only "Study {id}")
- On first message from browse, pass `ctxStudies` as `selected_studies` and persist pins if applicable

**Enter → new global chat**

- Extend `onKeyDown`: if `view.type === 'browse' && Enter && input.trim()`, run send flow
- Relax `canSend` / `disabled` for browse (or dedicated handler): `setView({ type: 'global-chat', chatId: null })` then existing `sendMessage` lazy-create
- Update composer placeholder for browse
- Manual test: Enter from browse with/without context chips; Shift+Enter still inserts newline

**Related**

- Consider fixing TKT-004 (pin after SSE done) if batch-pin on send is added

### Files Changed

- `ezredbiom/Experiment/frontend/js/app_render.js`
- `ezredbiom/Experiment/frontend/js/app_state.js`
- `ezredbiom/Experiment/frontend/style.css`
- `ezredbiom/Experiment/backend/routes/global_chat_routes.py`
- `ezredbiom/Experiment/backend/sql_store_cache.py` (reuse only; no schema change)

---

## TKT-007: Refactor Away from qiita_db.TRN / qiita_core (then delete both packages)

**Severity:** Low
**Status:** Open

### Description

The live app depends on exactly two files outside `ezredbiom/`:

- `qiita_db/sql_connection.py` — provides the `TRN` PostgreSQL transaction context manager
- `qiita_core/configuration_manager.py` — pulled in transitively by `sql_connection.py`

Everything else in `qiita_db/` (~15 MB, 80+ modules) and `qiita_core/` is dead.

### Plan

- Identify the 3 backend files that `from qiita_db.sql_connection import TRN`
- Replace `TRN` usage with raw `psycopg2` connection or a thin local wrapper
(see commit `ed3fc3d8` for an existing template)
- Remove `qiita_db` and `qiita_core` imports from those files
- Verify tests pass and no `ImportError` at startup
- `git rm -r qiita_db/ qiita_core/`

### Files Changed

- The 3 ezredbiom backend files using `TRN` (in `routes/` or `helpers/`)
- `qiita_db/` — delete after refactor
- `qiita_core/` — delete after refactor

---

## TKT-009: DuckDB, MIINT

**Severity:** Low
**Status:** Open

### Description

The live app depends on exactly two files outside `ezredbiom/`:

- `qiita_db/sql_connection.py` — provides the `TRN` PostgreSQL transaction context manager
- `qiita_core/configuration_manager.py` — pulled in transitively by `sql_connection.py`

Everything else in `qiita_db/` (~15 MB, 80+ modules) and `qiita_core/` is dead.

### Plan

- Identify the 3 backend files that `from qiita_db.sql_connection import TRN`
- Replace `TRN` usage with raw `psycopg2` connection or a thin local wrapper
(see commit `ed3fc3d8` for an existing template)
- Remove `qiita_db` and `qiita_core` imports from those files
- Verify tests pass and no `ImportError` at startup
- `git rm -r qiita_db/ qiita_core/`

### Files Changed

- The 3 ezredbiom backend files using `TRN` (in `routes/` or `helpers/`)
- `qiita_db/` — delete after refactor
- `qiita_core/` — delete after refactor

---

## TKT-010: BIOM include

**Severity:** Low
**Status:** Open

### Description

The live app depends on exactly two files outside `ezredbiom/`:

- `qiita_db/sql_connection.py` — provides the `TRN` PostgreSQL transaction context manager
- `qiita_core/configuration_manager.py` — pulled in transitively by `sql_connection.py`

Everything else in `qiita_db/` (~15 MB, 80+ modules) and `qiita_core/` is dead.

### Plan

- Identify the 3 backend files that `from qiita_db.sql_connection import TRN`
- Replace `TRN` usage with raw `psycopg2` connection or a thin local wrapper
(see commit `ed3fc3d8` for an existing template)
- Remove `qiita_db` and `qiita_core` imports from those files
- Verify tests pass and no `ImportError` at startup
- `git rm -r qiita_db/ qiita_core/`

### Files Changed

- The 3 ezredbiom backend files using `TRN` (in `routes/` or `helpers/`)
- `qiita_db/` — delete after refactor
- `qiita_core/` — delete after refactor

---

---

## TKT-011: Split oversized files after /pin additions

**Severity:** Low
**Status:** Open

### Description

Two files now exceed the 500-line limit (both were already over before the /pin feature was added):

- `ezredbiom/backend/helpers/qiita_fetch.py` — 514 lines
- `ezredbiom/frontend/js/app_state.js` — 538 lines

### Plan

**qiita_fetch.py** — extract into two modules:
- `helpers/qiita_samples.py` — sample fetching/caching (`_fetch_full_sample_metadata`, `_get_or_fetch_full_samples`, `_fetch_sample_context_text`, `_build_full_samples_block`, `_build_samples_report_payload`, `_build_pinned_reports_context`)
- Keep `qiita_fetch.py` for study header / search / detect helpers; re-export for back-compat or update imports

**app_state.js** — extract into two modules:
- `app_actions.js` — action handlers (sendMessage, unpinStudy, enrichAllStudies, doSearch, modal helpers, chat navigation)
- Keep `app_state.js` for useState/derived/returned shape

### Files Changed

- `ezredbiom/backend/helpers/qiita_fetch.py`
- `ezredbiom/backend/helpers/qiita_samples.py` (new)
- `ezredbiom/frontend/js/app_state.js`
- `ezredbiom/frontend/js/app_actions.js` (new)

---

## TKT-012: Merge page request fan-outs

**Severity:** Low
**Status:** Open

### Description

Two spots in the merge page fan out parallel requests that could become expensive at scale:

1. **`MergesTab` mount** (`merge_workspace.js`, `MergesTab` component): on load, fetches the full workspace detail for every workspace in parallel via `Promise.all(list.map(...))`. Bounded to the user's workspace count but could add up.

2. **`GlobalBiomSelector` Smart Select** (`merge_artifacts.js`, `GlobalBiomSelector.handleApply`): when study details are missing, fetches all missing study details in parallel — up to 5 studies, but no rate limiting or request cancellation.

### Plan

- Lazy-load workspace detail in `MergesTab` only when the user expands/hovers a card (or batch-fetch on a short delay rather than immediately on mount).
- In `GlobalBiomSelector`, fetch missing details sequentially or add a concurrency limit (e.g. `p-limit(3)`).

---

*Generated: 2026-05-19 | Updated: 2026-06-09*