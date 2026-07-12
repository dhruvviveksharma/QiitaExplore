# Changes — Qiita Explorer UI (March 2026)

All changes were made to `qiita_explore/` during this session.

---

## 1. Enriched Study Data (Prep / Sample / Artifact Info)

### Problem
Study preview cards and the LLM only had basic metadata (title, abstract, PI). No information about data types, number of samples, prep templates, or artifact file paths.

### Files Changed

#### `qiita_explore/backend/services/study_service.py`
- Rewrote `search_studies_with_sql()` SELECT to include three correlated subqueries:
  - `num_samples` — `COUNT(*)` from `qiita.study_sample`
  - `data_types` — `STRING_AGG(DISTINCT ...)` from `qiita.study_prep_template → data_type`
  - `num_preps` — `COUNT(DISTINCT prep_template_id)` from `qiita.study_prep_template`
- Also adds `study_alias` and `metadata_complete` from `qiita.study`
- Caps search results at `LIMIT 50`

#### `qiita_explore/backend/run.py`

**`first_studies()`**
- Same enriched correlated subqueries added as `search_studies_with_sql()` above

**`_study_detail_block()` (LLM context builder)**
- Now includes `Data Types`, `Num Samples`, `Num Preps`, and a per-prep breakdown (up to 5 preps) in the text block passed to the LLM
- This means the LLM can now answer questions like "How many samples does this study have?" or "What data types were used?"

**New helper functions**
- `_fetch_study_samples(study_id, limit=200)` — queries `qiita.study_sample JOIN qiita.sample_{study_id}` for sample-level metadata (sample_id, anonymized_name, collection_timestamp, env_package)
- `_fetch_prep_metadata_summary(prep_template_id)` — queries `qiita.prep_{prep_id}` for one row of sequencing metadata (platform, target_gene, instrument_model)
- `_fetch_study_detail_from_qiita(study_id)` — runs `prep.sql` and `artifacts.sql` patterns to return full prep template list + BIOM artifact paths

**New endpoint: `GET /api/studies/<id>/detail`**
- Returns `preps`, `artifacts`, `samples`, `total_samples` for a given study
- Preps + artifacts are cached in SQLite for 6 hours (TTL) to avoid repeated Qiita queries
- Each prep object is enriched with platform, target_gene, instrument_model from prep metadata

#### `qiita_explore/backend/sql_store.py`
- Added `study_detail_cache` table: `(study_id INTEGER PK, preps_json TEXT, artifacts_json TEXT, cached_at TEXT)`
- Added schema migration: `ALTER TABLE project_studies ADD COLUMN data_types TEXT / num_samples INTEGER / num_preps INTEGER / preps_json TEXT` (safe — no-ops if columns already exist)
- Updated `add_study_to_project()` INSERT to include the four new columns
- Updated `_load_project_studies()` SELECT to return the four new columns
- Added `get_study_detail_cache(study_id)` — returns None if cache is older than 6 hours
- Added `upsert_study_detail_cache(study_id, preps_json, artifacts_json)`
- Added `update_project_study_data(project_id, study_id, *, data_types, num_samples, num_preps, preps_json)` — updates enriched columns for an existing project study using `COALESCE` (only fills NULLs)

#### `qiita_explore/backend/store.py`
- Re-exports `get_study_detail_cache`, `upsert_study_detail_cache`, `update_project_study_data`

---

## 2. Non-Blocking Study Add + Enrich-All Endpoint

### Problem
`api_add_study()` was calling expensive Qiita queries synchronously, making "Add to Project" feel like a page freeze. Also, studies added before this session had NULL for all enriched columns.

### Files Changed

#### `qiita_explore/backend/run.py`

**Refactored `api_add_study()`**
- Now inserts the study into `project_studies` immediately and returns the updated project to the frontend
- Kicks off enrichment (`_enrich_study_in_project`) in a background `ThreadPoolExecutor` thread (non-blocking)

**New `_enrich_study_in_project(project_id, study_id)`**
- Fetches `num_samples` directly from `qiita.study_sample`
- Fetches prep detail via `_fetch_study_detail_from_qiita()` (checks SQLite cache first)
- Derives `data_types` and `num_preps` from fetched preps
- Calls `update_project_study_data()` to update the `project_studies` row

**New endpoint: `POST /api/projects/<project_id>/studies/enrich-all`**
- Re-enriches all studies in a project (fixes studies added before this code existed)
- Runs `_enrich_study_in_project` for each study via ThreadPool, waits for all to finish
- Returns `{ ok, updated, project }` with the fully enriched project

---

## 3. No Page Re-renders / Optimistic UI Updates

### Problem
Several actions caused unnecessary full re-fetches of the project or global chats, creating visible flicker (perceived as page reload).

### Files Changed

#### `qiita_explore/frontend/app.js`

| Function | Before | After |
|---|---|---|
| `addStudyToProject()` | POSTed, ignored response, called `fetchProjectDetail()` | Uses POST response body directly → `setOpenProject(updated)` |
| `removeStudy()` | DELETEd, ignored response, called `fetchProjectDetail()` | Uses DELETE response body directly → `setOpenProject(updated)` |
| `newProjChat()` | Created chat, called `fetchProjectDetail()` | Prepends new chat to `openProject.chats` in state |
| `deleteProjChat()` | Deleted chat, called `fetchProjectDetail()` | Filters deleted chat out of `openProject.chats` in state |
| `sendMessage()` project-chat `onDone` | Called `fetchProjectDetail()` to update sidebar title | Updates `openProject.chats[].title` and `chatCache[chatId].title` locally with `msg.slice(0,60)` |
| `newGlobChat()` | Called `loadGlobalChats()` | Prepends new chat to `globalChats` state |
| `deleteGlobChat()` | Called `loadGlobalChats()` | Filters deleted chat from `globalChats` state |
| `sendMessage()` global-chat `onDone` | Called `loadGlobalChats()` | Updates matching entry in `globalChats` state |

---

## 4. Study Modal — Samples, Prep Metadata, Artifacts + Download

### Problem
The study modal only showed title, abstract, and PI info. No prep templates, samples, or artifact paths.

### Files Changed

#### `qiita_explore/frontend/app.js`

**`openStudyModal()` rewrite**
- Now uses `AbortController` — if the modal is closed before the detail fetch resolves, the fetch is cancelled and no spurious state update fires
- New `closeModal()` function used by both overlay click and × button

**New `enrichAllStudies(projId)` function**
- POSTs to `/api/projects/<id>/studies/enrich-all`
- On success, updates `openProject` state with the enriched project (no full reload)

**Study Cards (Browse grid)**
- Now shows `dtype-chip` badges for each data type (e.g. "16S rRNA", "WGS")
- Shows `"124 samples · 3 preps"` meta line below the abstract

**Study Modal — new sections**
| Section | Contents |
|---|---|
| Quick stats row | Data type chips + sample count + prep count |
| Prep Templates | Table with: Prep ID, Data Type, Investigation Type, Platform, Target Gene, Status |
| Samples | Scrollable table (max 200 rows, total count shown): Sample ID, Anonymized Name, Env Package, Collection Date |
| Artifacts | Table with: Artifact ID, Type, Data Type, truncated file path + ⎘ Copy button (copies full path to clipboard) |

**Sources tab**
- Added **↻ Refresh Data** button that triggers `enrichAllStudies()` for the current project

#### `qiita_explore/frontend/style.css`
- `.dtype-chip` — orange pill badge for data type labels on cards and modal
- `.modal-stats` — quick stats row in modal header
- `.prep-table` — shared table style for preps, samples, artifacts
- `.samples-table-wrap` — scrollable container with sticky header (max 280px height)
- `.artifact-path-cell` — flex row with truncated monospace path + copy button
- `.btn-copy-path` — small copy icon button
- `.sources-tab-header` / `.folder-refresh-btn` — refresh button in Sources tab

---

## Architecture Notes

### Data Flow (after changes)
```
Qiita PostgreSQL
  ↓ enriched study query (data_types, num_preps, num_samples correlated subqueries)
GET /api/studies/first  &  POST /api/search
  ↓ study cards show: data types · N samples · N preps
  ↓ user clicks "Add to Project"
POST /api/projects/<id>/studies  →  returns immediately (fast)
  ↓ background thread: fetches num_samples + preps → update project_studies
  ↓ user clicks ↻ Refresh Data (or re-adds study) → project_studies updated
GET /api/projects/<id>  →  studies now have full enriched data
  ↓
_build_project_study_context() → _study_detail_block()
  ↓ LLM context includes: Data Types, Num Samples, Num Preps, per-prep breakdown
  ↓ LLM can answer: "How many samples?", "What data types?", "What instrument?"
```

### Efficiency Strategy for Large Qiita DB (50TB+)
- All browse queries use `LIMIT` (search capped at 50, browse at configurable N)
- Study detail (preps + artifacts) cached in SQLite for 6 hours — avoids repeated Qiita JOINs
- Sample data fetched lazily (only on modal open), never on page load
- Background enrichment thread keeps the HTTP response fast
- Recommended (not implemented): connection pooling, read replica, PostgreSQL materialized view for data_types/num_preps aggregations

### Server Restart Required
All Python changes require restarting the Flask backend:
```bash
cd qiita_explore/backend
python run.py
```
After restart, click **↻ Refresh Data** in the Sources tab of any project to populate enriched data for previously-added studies.
