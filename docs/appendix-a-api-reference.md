# Appendix A — API Reference

Complete reference for the QiitaExplore HTTP surface: 71 endpoints (counted from `@app.route`, 2026-09-24), all under `/api/`.

This appendix is the single source of truth for the HTTP surface. It is derived directly from the seven route modules in `backend/routes/` plus the auth guard in `backend/helpers/auth_middleware.py`. If an endpoint is not listed here, it does not exist; if the behavior described here disagrees with the code, the code is right and this file needs updating.

---

## Conventions

### Base path

Every route is registered under `/api/`. The Flask app is constructed in `backend/run.py :: app`, which imports all seven route modules solely for their `@app.route` side effects. There are no non-`/api/` routes and no blueprints — every view function is registered on the bare `app` object.

Backend runs under Gunicorn on port 5001 (`qiita_explore/start_barnacle.sh`). CORS is enabled with `supports_credentials=True` against `config.ALLOWED_ORIGINS`.

### Auth: default-deny

Two `before_request` hooks are registered together by `backend/helpers/auth_middleware.py :: register_auth_middleware`:

1. `_load_session` — reads the `qe_sid` cookie, resolves it to a session row, and sets `g.user_id` / `g.session_row`. No remote Qiita calls.
2. `_require_auth` — default-deny. Any endpoint not in the public set gets **401** when `g.user_id` is `None`.

**Exactly three endpoints are public**, matched by **exact Flask endpoint name** — never by path prefix. From `backend/helpers/auth_middleware.py :: PUBLIC_ENDPOINTS`:

- `api_auth_login_url`
- `api_auth_connect`
- `api_auth_me`

Note that `api_auth_logout`, `api_auth_legacy_default`, and `api_auth_claim_default` live under `/api/auth/` but are **not** public — they require a session like everything else. The comment in the source is explicit that prefix matching is the hole this guard exists to close.

`OPTIONS` requests bypass both hooks. Requests to unrouted paths (`request.endpoint is None`) fall through to Flask's normal 404.

### Connect-time failures

`POST /api/auth/connect` calls `whoami` once. If Qiita is unreachable, connect returns **503** `{"error": "Qiita is temporarily unreachable, try again shortly"}`. If the PAT is invalid, connect returns **401**. Mid-session requests do not call Qiita.

### CSRF

Every `POST`, `PUT`, `PATCH`, and `DELETE` to a non-public endpoint requires an `X-CSRF-Token` header whose value matches the session's stored `csrf_token`, compared with `hmac.compare_digest`. A missing or mismatched token yields **403** `{"error": "CSRF token missing or invalid"}`. An absent stored token also fails closed.

Clients get the token from `api_auth_connect` (on login) or `api_auth_me` (on rehydrate).

### Error envelope

Errors are a flat JSON object with a single `error` key holding a human-readable string:

```json
{ "error": "Project not found" }
```

There is no error code, type, or nested detail field in the general case. Two deviations exist:

- `api_auth_connect` adds a `detail` key (`"TypeName: message"`) on unexpected 500s, but only when `config.DEBUG_ERROR_DETAIL` is set via `QIITA_EXPLORE_DEBUG_ERRORS`.
- Several merge/validate handlers add a plural `errors` array alongside `error` — see `submit_merge_job`.

Some handlers pass the raw exception text through as `str(e)` (notably `api_study_detail`, `search`, `api_first_studies`, `api_sample_detail`). Treat those strings as diagnostic, not stable.

### SSE

Exactly two endpoints stream. Both are `POST` and both return `text/event-stream`:

- `POST /api/projects/<project_id>/chats/<chat_id>/message/stream`
- `POST /api/global-chats/<chat_id>/message/stream`

The response is built by `backend/helpers/request_utils.py :: sse_response`, which sets `mimetype='text/event-stream'` plus headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Frames are emitted by `backend/helpers/llm_helpers.py :: _sse` in the form `event: <name>\ndata: <json>\n\n`. Bare `: keepalive\n\n` comment lines are interleaved at points where a slow step could otherwise idle the connection.

Validation failures (bad `report_study_id`, missing `message`, unknown chat) are returned as ordinary JSON with a 4xx status **before** the stream opens. Once the stream is open the status is already 200, so downstream failures arrive as an `error` event rather than an HTTP error.

---

## Summary table

### Auth — `backend/routes/auth_routes.py` (6)

| Method | Path | Flask endpoint | Auth | Purpose |
|---|---|---|---|---|
| GET | `/api/auth/login-url` | `api_auth_login_url` | public | Build the Qiita control-plane login URL |
| POST | `/api/auth/connect` | `api_auth_connect` | public | Exchange a pasted PAT for a session cookie |
| GET | `/api/auth/me` | `api_auth_me` | public | Current identity, or `{anonymous: true}` |
| POST | `/api/auth/logout` | `api_auth_logout` | session | Revoke the session and clear the cookie |
| GET | `/api/auth/legacy-default` | `api_auth_legacy_default` | session | Whether legacy `"default"` data can be claimed |
| POST | `/api/auth/claim-default` | `api_auth_claim_default` | session | Claim legacy `"default"`-owned rows |

### Studies — `backend/routes/study_routes.py` (8)

| Method | Path | Flask endpoint | Auth | Purpose |
|---|---|---|---|---|
| GET | `/api/studies/<int:study_id>/detail` | `api_study_detail` | session | Preps, artifacts, artifact graph, samples |
| GET | `/api/studies/<int:study_id>/samples/<path:sample_id>` | `api_sample_detail` | session | All metadata fields for one sample |
| POST | `/api/search` | `search` | session | Browse search: regex planner, relevance ranking, facet filters |
| GET | `/api/search/facets` | `api_search_facets` | session | PI / data-type / year-added option lists for the Browse filters |
| GET | `/api/systems` | `api_systems` | session | Live health probe of every allowed model |
| GET | `/api/settings` | `api_get_settings` | session | Whether an Anthropic key is stored |
| POST | `/api/settings` | `api_post_settings` | session | Store an Anthropic API key |
| GET | `/api/studies/first` | `api_first_studies` | session | First N public studies for the browse grid |

### Projects — `backend/routes/project_routes.py` (8)

| Method | Path | Flask endpoint | Auth | Purpose |
|---|---|---|---|---|
| GET | `/api/projects` | `api_list_projects` | session | List the caller's projects |
| POST | `/api/projects` | `api_create_project` | session | Create a project |
| GET | `/api/projects/<project_id>` | `api_get_project` | session | One project with its studies |
| DELETE | `/api/projects/<project_id>` | `api_delete_project` | session | Delete a project |
| POST | `/api/projects/<project_id>/studies` | `api_add_study` | session | Add a study, enrich in background |
| POST | `/api/projects/<project_id>/studies/enrich-all` | `api_enrich_all_studies` | session | Re-enrich every study in a project |
| DELETE | `/api/projects/<project_id>/studies/<int:study_id>` | `api_remove_study` | session | Remove a study from a project |
| POST | `/api/projects/<project_id>/preload` | `api_project_preload` | session | Warm the full-samples cache |

### Project chats — `backend/routes/chat_routes.py` (6)

| Method | Path | Flask endpoint | Auth | Purpose |
|---|---|---|---|---|
| POST | `/api/projects/<project_id>/chats` | `api_create_chat` | session | Create a chat, optionally answering a first message |
| GET | `/api/projects/<project_id>/chats/<chat_id>` | `api_get_chat` | session | One chat with messages |
| DELETE | `/api/projects/<project_id>/chats/<chat_id>` | `api_delete_chat` | session | Delete a chat |
| POST | `/api/projects/<project_id>/chats/<chat_id>/message/stream` | `api_chat_message_stream` | session | **SSE** project chat turn |
| POST | `/api/projects/<project_id>/chats/<chat_id>/pinned/<int:study_id>` | `api_pin_project_chat_study` | session | Pin a study to a project chat |
| DELETE | `/api/projects/<project_id>/chats/<chat_id>/pinned/<int:study_id>` | `api_unpin_project_chat_study` | session | Unpin a study |

### Global chats — `backend/routes/global_chat_routes.py` (7)

| Method | Path | Flask endpoint | Auth | Purpose |
|---|---|---|---|---|
| GET | `/api/global-chats` | `api_list_global_chats` | session | List global chats |
| POST | `/api/global-chats` | `api_create_global_chat` | session | Create a global chat |
| GET | `/api/global-chats/<chat_id>` | `api_get_global_chat` | session | One global chat with messages |
| DELETE | `/api/global-chats/<chat_id>` | `api_delete_global_chat` | session | Delete a global chat |
| POST | `/api/global-chats/<chat_id>/message/stream` | `api_global_chat_message_stream` | session | **SSE** global chat turn (agentic) |
| POST | `/api/global-chats/<chat_id>/pinned/<int:study_id>` | `api_pin_global_chat_study` | session | Pin a study to a global chat |
| DELETE | `/api/global-chats/<chat_id>/pinned/<int:study_id>` | `api_unpin_global_chat_study` | session | Unpin a study |

### Merge — `backend/routes/merge_routes.py` (14)

| Method | Path | Flask endpoint | Auth | Purpose |
|---|---|---|---|---|
| GET | `/api/merge-workspaces` | `list_merge_workspaces` | session | List merge workspaces |
| POST | `/api/merge-workspaces` | `create_merge_workspace` | session | Create a workspace (**201**) |
| GET | `/api/merge-workspaces/<workspace_id>` | `get_merge_workspace` | session | One workspace with its study slots |
| DELETE | `/api/merge-workspaces/<workspace_id>` | `delete_merge_workspace` | session | Delete a workspace |
| PATCH | `/api/merge-workspaces/<workspace_id>` | `patch_merge_workspace` | session | Rename a workspace |
| POST | `/api/merge-workspaces/<workspace_id>/studies` | `add_study_to_merge_workspace` | session | Add a study slot, max 5 (**201**) |
| DELETE | `/api/merge-workspaces/<workspace_id>/studies/<int:study_id>` | `remove_study_from_merge_workspace` | session | Remove a study slot |
| PATCH | `/api/merge-workspaces/<workspace_id>/studies/<int:study_id>` | `update_merge_workspace_study` | session | Set chosen artifacts / sample filter |
| GET | `/api/merge-workspaces/<workspace_id>/validate` | `validate_merge_workspace` | session | Compatibility check + merge preview |
| GET | `/api/merge-workspaces/<workspace_id>/samples` | `get_workspace_samples` | session | Per-study BIOM sample counts |
| GET | `/api/merge-workspaces/<workspace_id>/studies/<int:study_id>/samples` | `get_workspace_study_samples` | session | Paged samples with metadata |
| POST | `/api/merge-workspaces/<workspace_id>/jobs` | `submit_merge_job` | session | Validate and queue a merge job (**202**) |
| GET | `/api/merge-workspaces/<workspace_id>/jobs` | `get_workspace_jobs` | session | List jobs for a workspace |
| GET | `/api/merge-jobs/<job_id>` | `poll_merge_job` | session | Poll one job's status |

### Artifacts — `backend/routes/artifact_routes.py` (4)

| Method | Path | Flask endpoint | Auth | Purpose |
|---|---|---|---|---|
| GET | `/api/artifacts/<int:artifact_id>/samples` | `get_artifact_samples` | session | Sample IDs + metadata for a BIOM artifact |
| POST | `/api/artifacts/sample-counts` | `get_artifact_sample_counts` | session | Batch `{artifact_id: count}` |
| GET | `/api/artifacts/<int:artifact_id>/files/<int:filepath_id>/download` | `download_artifact_file` | session | Download one file from an artifact |
| GET | `/api/merge-jobs/<job_id>/download` | `download_merge_result` | session | Download a finished merge tarball |

### Aggregations — `backend/routes/aggregation_routes.py` (11)

| Method | Path | Flask endpoint | Auth | Purpose |
|---|---|---|---|---|
| GET | `/api/aggregations` | `api_list_aggregations` | session | The caller's aggregations, studies and selected-sample counts embedded |
| POST | `/api/aggregations` | `api_create_aggregation` | session | Create an aggregation (`{name}`) |
| PATCH | `/api/aggregations/<aggregation_id>` | `api_update_aggregation` | session | Rename and/or save the Data type / Processing `file_filter` |
| GET | `/api/aggregations/<aggregation_id>/file-facets` | `api_aggregation_file_facets` | session | Picker options (sample counts) + how many checked samples the export would contain |
| DELETE | `/api/aggregations/<aggregation_id>` | `api_delete_aggregation` | session | Delete (cascades to studies and samples) |
| POST | `/api/aggregations/<aggregation_id>/studies` | `api_add_study_to_aggregation` | session | Add a whole study — every sample checked |
| DELETE | `/api/aggregations/<aggregation_id>/studies/<int:study_id>` | `api_remove_study_from_aggregation` | session | Remove a study and its checked samples |
| GET | `/api/aggregations/<aggregation_id>/studies/<int:study_id>/samples` | `api_aggregation_study_samples` | session | One page of the study's samples, files-first under the `file_filter`, with `selected`/`fastq`/`fasta`/`data_types` |
| PATCH | `/api/aggregations/<aggregation_id>/studies/<int:study_id>/samples` | `api_set_aggregation_samples` | session | Check / uncheck samples (`add` / `remove` or `select`) |
| GET | `/api/aggregations/<aggregation_id>/export.csv` | `download_aggregation_csv` | session | CSV (for scripts) of the checked samples' per-sample sequence files under the `file_filter` |
| GET | `/api/aggregations/<aggregation_id>/export.xlsx` | `download_aggregation_xlsx` | session | The same rows as an Excel workbook — `sample_id` stays text |

---

## Auth

### api_auth_login_url

`GET /api/auth/login-url` — public.

Returns `{"url": "..."}`. The URL is `{QIITA_PUBLIC_LOGIN_URL}/api/v1/auth/login`. When `QIITA_LOGINROCKET_URL` is configured, that is wrapped in a LoginRocket `/logout?redirect_uri=...` first, so a cached AuthRocket session cannot hijack the login into completing as the previously-cached user. (`backend/routes/auth_routes.py :: api_auth_login_url`)

### api_auth_connect

`POST /api/auth/connect` — public.

Exchanges a pasted Qiita personal access token for a session. This is the only endpoint that mints a session cookie.

Request:

```json
{ "token": "<qiita personal access token>" }
```

Behavior, in order:

1. **Origin check.** If `config.ALLOWED_ORIGINS` is non-empty, the `Origin` header must appear in it exactly, else **403** `{"error": "origin not allowed"}`. An empty allowlist means same-origin deployment behind the proxy and the check is skipped.
2. Body must be a JSON object (**400**) with a non-empty `token` (**400**).
3. `whoami(pat)` against Qiita. Transient failure → **503**; rejection → **401** `{"error": "invalid or unrecognized Qiita token"}`; a response with no `principal_idx` → **502** `{"error": "unexpected identity response from Qiita"}`.
4. Upsert the user and create the session. If a valid session cookie was already present, the prior session is revoked in the same transaction.

On success, sets the `qe_sid` cookie (`HttpOnly`, `SameSite=Lax`, `Secure` per `config.SESSION_COOKIE_SECURE`, `max_age = AUTH_SESSION_ABSOLUTE_TTL_SECONDS`) and returns the identity payload plus the CSRF token:

```json
{
  "user_id": "...", "email": "...", "system_role": "...",
  "scopes": [], "profile_complete": true,
  "claim_eligible": false, "csrf_token": "..."
}
```

`backend/routes/auth_routes.py :: api_auth_connect`

### api_auth_me

`GET /api/auth/me` — public.

Returns `{"anonymous": true}` when there is no session, or when the session resolves to a user row that no longer exists. Otherwise returns the same identity payload as `api_auth_connect`, with `csrf_token` read from the session row. This is the rehydrate path on page load. (`backend/routes/auth_routes.py :: api_auth_me`)

### api_auth_logout

`POST /api/auth/logout` — session + CSRF.

Revokes the session row if present and deletes the cookie. Returns `{"ok": true}`. Not public: a caller with no valid session gets 401 from the guard before the handler runs. (`backend/routes/auth_routes.py :: api_auth_logout`)

### api_auth_legacy_default

`GET /api/auth/legacy-default` — session.

Returns `{"eligible": <bool>}`, plus `"counts"` from `legacy_default_counts()` when eligible. Used to decide whether to offer the claim flow. (`backend/routes/auth_routes.py :: api_auth_legacy_default`)

### api_auth_claim_default

`POST /api/auth/claim-default` — session + CSRF.

Reassigns rows still owned by the pre-auth `"default"` user to the calling user — the one-time migration for data created before real identities existed.

No request body is read. Delegates to `claim_legacy_default(g.user_id)`:

- `ValueError` → **403** `{"error": "not eligible to claim"}`
- `LegacyClaimConflict` → **409** `{"error": "legacy data already claimed"}`

Success returns the per-table counts that were transferred:

```json
{ "ok": true, "claimed": { "projects": 3, "global_chats": 7 } }
```

The exact keys in `claimed` come from `store/legacy_claim.py`, not from this handler.

`backend/routes/auth_routes.py :: api_auth_claim_default`

---

## Studies

### api_study_detail

`GET /api/studies/<int:study_id>/detail` — session.

The heaviest read in the app, and the one place the multi-layer `study_detail_cache` is assembled. Returns **404** `{"error": "Study not found or not public"}` when `is_study_public(study_id)` is false, and **500** with `str(e)` if the Qiita fetch raises.

Cache assembly proceeds in four independent stages, each of which can hit or miss separately and writes back on miss:

1. **Preps + artifacts** — from `preps_json` / `artifacts_json`, else `_fetch_study_detail_from_qiita`.
2. **Artifact graph** — from `artifact_graph_json`. A cached graph is discarded as stale if artifact nodes lack a `filepaths` key or job nodes lack `command_params`, then re-fetched via `fetch_artifact_graph`.
3. **Prep metadata** — from `prep_metadata_json`, else fanned out over a `ThreadPoolExecutor` (max 8 workers) calling `_fetch_prep_metadata_summary` per prep id, and merged into each prep dict in place.
4. **Samples** — from `samples_json` / `total_samples`, else `_fetch_study_samples(study_id, limit=200)`. A malformed cached blob falls back to a live fetch.

A fifth step populates `samples_context` (the LLM context text) when absent. Response:

```json
{
  "study_id": 10317, "preps": [], "artifacts": [],
  "artifact_graph": [], "samples": [], "total_samples": 1234,
  "cached": true
}
```

`cached` reflects only whether the **preps/artifacts** stage hit — the other stages can still have missed and re-fetched. Samples are capped at 200 regardless of `total_samples`.

`backend/routes/study_routes.py :: api_study_detail`

### api_sample_detail

`GET /api/studies/<int:study_id>/samples/<path:sample_id>` — session.

Returns `{"sample_id": "...", "fields": {...}}` for one sample, reading `sample_values` from `qiita.sample_{study_id}`. Drops the internal `qiita_study_id` key. **404** for a non-public study or an unknown sample; **500** with `str(e)` on query failure. `sample_id` uses `<path:>` so it may contain slashes. (`backend/routes/study_routes.py :: api_sample_detail`)

### search

`POST /api/search` — session + CSRF.

The browse-grid search. A regex planner (`browse_query_to_sql` — no LLM, despite the module name) turns the text into a SQL `WHERE` fragment; optional facet filters narrow it; results are ranked by relevance.

Request (`filters` and every key inside it are optional; `{"query": q}` alone still works):

```json
{ "query": "550 mouse gut", "deep_search": true,
  "filters": { "pis": ["Rob Knight"], "data_types": ["16S"], "year_min": 2012, "year_max": 2020 } }
```

Empty `query` **with no filters** → **400** `{"error": "Query is required"}`. Empty `query` with filters is filter-only browsing (LIMIT 120, ordered by sample count). A non-integer `year_min`/`year_max` → **400**. `pis` is capped at 50 names, `data_types` at 20; an inverted year range is swapped.

Behavior:

1. `browse_query_to_sql(user_query)` returns a plan dict with `where_clause`, `params`, `search_limit`, `keywords`, `study_ids`, `id_only`, `phrase`, `applied_filters`, and PI resolution metadata. Bare integers are study IDs, never keywords; an `id_only` plan is exactly `s.study_id = ANY(%s)`.
2. `build_browse_filter_where` ANDs PI names / year bounds into that WHERE (`sp_pi.name = ANY(%s)`, `EXTRACT(YEAR FROM s.first_contact) >= / <= %s`); data types pass through `search_studies_with_sql(data_types=...)`.
3. `search_studies_with_sql(...)` runs the text search with expanded keywords as `relevance_keywords` (title 30, alias 15, PI 20, abstract 10 per hit, +1000 for a study whose id was in the query, +25 when the whole query appears in the title). PI veto applies only when `resolve_pi` finds a match in `study_person`.
4. When `deep_search` is true **and** the plan is not `id_only` **and** there are terms to probe, a second pass runs `search_studies_by_sample_meta` over per-study `sample_{id}` JSONB, bounded by `SAMPLE_SEARCH_DEEP_CANDIDATES` (default 500) and narrowed by the filters' data types / PIs / years. Merged rows are then gated exactly by `study_passes_browse_filters` and re-ranked by `finalize_search_results` (same weights, +1 per keyword with a sample-metadata hit).

Response echoes the plan, the PI `applied_filters`, and the normalised `filters` (`null` when none were sent); every row carries `year` (year the study was added to Qiita):

```json
{
  "results": [{ "study_id": 550, "study_title": "…", "year": 2015, "relevance": 1030, "...": "..." }],
  "sql_query": { "keywords": ["mouse", "gut"], "study_ids": [550], "id_only": false, "phrase": "mouse gut",
                 "applied_filters": { "pi": { "input": [], "resolved": [], "veto_applied": false } } },
  "applied_filters": { "pi": { "input": [], "resolved": [], "veto_applied": false } },
  "filters": { "pis": ["Rob Knight"], "data_types": ["16S"], "year_min": 2012, "year_max": 2020 },
  "count": 1
}
```

Any exception is caught, traceback-printed, and returned as **500** with `str(e)`.

`backend/routes/study_routes.py :: search`

### api_search_facets

`GET /api/search/facets` — session.

Option lists for the Browse filter pickers, over public studies only, recomputed at most every 6 h per Gunicorn worker (`services/browse_filters.py :: get_search_facets`). PIs are grouped by `study_person.name` — the same column the `pis` filter matches on.

```json
{
  "pis": [{ "name": "Rob Knight", "count": 312 }],
  "years": { "min": 2011, "max": 2026 },
  "data_types": [{ "name": "16S", "count": 1450 }]
}
```

`backend/routes/study_routes.py :: api_search_facets`

### api_systems

`GET /api/systems` — session.

Live health check of every model in `config.ALLOWED_MODELS`. Not cached — each call actually hits the LLM endpoint.

For every model in `ALLOWED_MODELS`, `_probe_model` issues a real one-token completion (`"Hi"`, `max_tokens=1`, `timeout=15`) and times it. Any exception marks the model `"down"`; the elapsed time is still reported. All probes run concurrently on a `ThreadPoolExecutor` sized to the model count, so wall time is roughly the slowest probe, bounded near the 15s timeout.

Results are merged with `MODEL_METADATA` and sorted with `tier == "main"` models first, then alphabetically. Returns a JSON **array**, not an object:

```json
[ { "name": "qwen3", "status": "ok", "latency_ms": 412, "tier": "main" } ]
```

Because this fans out a live request per model on every call, it is a page-load-sensitive endpoint.

`backend/routes/study_routes.py :: api_systems`

### api_get_settings

`GET /api/settings` — session.

Returns `{"anthropic_key_set": <bool>}`. The key value itself is never returned. (`backend/routes/study_routes.py :: api_get_settings`)

### api_post_settings

`POST /api/settings` — session + CSRF.

Body `{"anthropic_api_key": "..."}`. A blank or whitespace-only value is ignored rather than clearing the stored key. Always returns `{"ok": true}`, including when nothing was written. The value is stored via `set_setting` in the global `meta` table — see the note in the closing findings about scoping. (`backend/routes/study_routes.py :: api_post_settings`)

### api_first_studies

`GET /api/studies/first?limit=20` — session.

Backs the initial browse grid. Returns `{"results": [], "count": N, "limit": N}`. The reported `limit` is clamped to 1–100 and falls back to 20 for non-numeric input, but note the clamp is applied to the **echoed** value; `first_studies(limit=limit)` receives the raw argument. **500** with `str(e)` on failure. (`backend/routes/study_routes.py :: api_first_studies`)

---

## Projects

### api_list_projects

`GET /api/projects` — session. Returns `{"projects": [...]}` scoped to `g.user_id`. Each row carries `project_id`, `name`, `created_at`, `updated_at`, `studies_count`, `chats_count`. (`backend/routes/project_routes.py :: api_list_projects`)

### api_create_project

`POST /api/projects` — session + CSRF. Body `{"name": "..."}`; blank or missing becomes `"Untitled"`. Returns the created project object, or **500** `{"error": "Failed to create project"}`. (`backend/routes/project_routes.py :: api_create_project`)

### api_get_project

`GET /api/projects/<project_id>` — session. Returns the project with its `studies` array. **404** if the project does not exist *or* is owned by another user — `get_project` is owner-scoped with no cross-user fallback. (`backend/routes/project_routes.py :: api_get_project`)

### api_delete_project

`DELETE /api/projects/<project_id>` — session + CSRF. Returns `{"ok": true}` unconditionally; deleting an unknown or unowned project is a silent no-op reported as success. (`backend/routes/project_routes.py :: api_delete_project`)

### api_add_study

`POST /api/projects/<project_id>/studies` — session + CSRF.

Body `{"study": {"study_id": 10317, ...}}`. Missing `study` or `study_id` → **400**. A non-public study → **403** `{"error": "Study is not public and cannot be added"}`. Unknown project → **404**.

Returns the updated project immediately, then submits `_enrich_study_in_project` to the shared background executor. This is what the no-refresh sidebar pattern consumes: the response body carries the new state, so the client patches React state rather than re-fetching. (`backend/routes/project_routes.py :: api_add_study`)

### api_enrich_all_studies

`POST /api/projects/<project_id>/studies/enrich-all` — session + CSRF.

Re-fetches sample counts and prep detail for every study in a project. Unlike `api_add_study`, this endpoint **blocks** until the background work finishes.

No request body. **404** if the project is not found or not owned. For each study with a non-null `study_id`, `_enrich_study_in_project` is submitted to `_bg_executor`, then every future is awaited with `f.result(timeout=30)` in a loop, with exceptions swallowed per future.

Two consequences worth knowing before calling it from the UI:

- The executor has 4 workers, and the timeouts are sequential rather than shared. A project of N studies can therefore block substantially longer than 30 seconds in aggregate.
- A future that times out is swallowed, so the enrichment may still be running in the background when the response returns. The `updated` count reports futures **submitted**, not enrichments that succeeded.

Per study, `_enrich_study_in_project` counts `qiita.study_sample` rows, resolves preps from the detail cache or Qiita, derives `data_types` as a sorted comma-joined string, writes `num_samples` / `num_preps` / `preps_json` back to `project_studies`, then populates `samples_context` if absent. Both cache steps are wrapped in bare `except Exception: pass`.

Response:

```json
{ "ok": true, "updated": 4, "project": { } }
```

`project` is a fresh `get_project` read, so it reflects whatever enrichment had committed by the time the response was built.

`backend/routes/project_routes.py :: api_enrich_all_studies`

### api_remove_study

`DELETE /api/projects/<project_id>/studies/<int:study_id>` — session + CSRF. Returns the updated project, or **404** if `remove_study_from_project` returns `None`. (`backend/routes/project_routes.py :: api_remove_study`)

### api_project_preload

`POST /api/projects/<project_id>/preload` — session + CSRF. Fire-and-forget cache warm: submits `_get_or_fetch_full_samples(sid, 500)` per study to the background executor. **404** for an unknown project. Returns `{"queued": [10317, 550]}` — study ids submitted, not completed. Non-integer study ids are skipped silently. (`backend/routes/project_routes.py :: api_project_preload`)

---

## Project Chats

> **There is no list-chats endpoint for project chats**, unlike global chats which have `GET /api/global-chats`. A project's chats are returned inline by `GET /api/projects/<project_id>` — see `backend/store/crud.py :: get_project`, which attaches them. The frontend relies on that and never requests a separate list.

### api_create_chat

`POST /api/projects/<project_id>/chats` — session + CSRF. Body accepts `message` or `first_message`, plus optional `model` and `title`. **404** for an unknown project. When a first message is present the handler runs a **blocking, non-streaming** `llm_chat` turn with project study context and persists both messages before responding. A `title`/`first_message` passed here is treated as user-set and never replaced by the background LLM-titling job (that job only runs from the streaming turn path — see [`06-streaming-and-chat.md`](06-streaming-and-chat.md)). Returns the full chat object. (`backend/routes/chat_routes.py :: api_create_chat`)

### api_get_chat

`GET /api/projects/<project_id>/chats/<chat_id>` — session. Returns the chat with `messages` and `pinned_studies`. **404** if not found for this user. (`backend/routes/chat_routes.py :: api_get_chat`)

### api_delete_chat

`DELETE /api/projects/<project_id>/chats/<chat_id>` — session + CSRF. Returns `{"ok": true}` unconditionally. (`backend/routes/chat_routes.py :: api_delete_chat`)

### api_chat_message_stream

`POST /api/projects/<project_id>/chats/<chat_id>/message/stream` — session + CSRF. **SSE.**

One of the two streaming endpoints. Request:

```json
{ "message": "compare the two 16S studies", "model": "qwen3",
  "report_study_id": null, "pin_study_ids": null }
```

`content` is accepted as an alias for `message`. Parsed by `backend/helpers/request_utils.py :: parse_chat_stream_body`, which returns **400** for a non-integer `report_study_id`, a `pin_study_ids` that is not a list of integers, or an empty message. **404** if the chat is not found. All of these precede the stream.

The generator opens with a keepalive and then takes exactly one of three branches:

**Pin branch** (`pin_study_ids` present) — delegates to `stream_pin_flow` with `SCOPE_PROJECT`, which emits `step_start`/`step_done` for `pin_studies` and `deep_context`, then `llm_generate` plus `token` events. Terminates with `done` carrying `pinned_studies` and returns early.

**Report branch** (`report_study_id` present) — rejects studies not in current `project_studies` membership with `step_start`/`step_done` and a streamed `token` refusal (no `ui` event). Member studies delegate to `stream_samples_report` as before.

**Normal branch** — emits `build_context`, builds project study context, merges detected/pinned study ids for deep context, then delegates to `stream_agent` with `PROJECT_TOOL_SCHEMAS` and `PROJECT_CHAT_SYSTEM_PROMPT`. Agent events (`agent_start`, `segment_*`, `token`) are accumulated into `agent_segments` for persistence. Agent turns include filtered `pinned_studies` and `pinned_study_meta` in `done`.

All branches converge on persisting the joined assistant text and emitting `done` with `persisted: true` (agent turns also carry pin metadata).

Exceptions inside the generator are logged and surfaced as an `error` event carrying `friendly_llm_error(e, model)`. Because headers are already sent, the HTTP status stays 200.

Note that the project studies used for context come from `get_project_studies_only(project_id)`, which is not user-scoped — the ownership guarantee for this route comes from the preceding `get_chat(project_id, user_id, chat_id)` check.

`backend/routes/chat_routes.py :: api_chat_message_stream`

### api_pin_project_chat_study

`POST /api/projects/<project_id>/chats/<chat_id>/pinned/<int:study_id>` — session + CSRF. **404** if the chat is not found. Returns `{"ok": true, "pinned_studies": [...]}` with the full post-pin list. Validation and the 10-pin cap live in `_pin_studies_validated`. (`backend/routes/chat_routes.py :: api_pin_project_chat_study`)

### api_unpin_project_chat_study

`DELETE /api/projects/<project_id>/chats/<chat_id>/pinned/<int:study_id>` — session + CSRF. **404** if the chat is not found. Returns `{"ok": true, "pinned_studies": [...]}`. (`backend/routes/chat_routes.py :: api_unpin_project_chat_study`)

---

## Global Chats

### api_list_global_chats

`GET /api/global-chats` — session. Returns `{"chats": [...]}` for `g.user_id`. (`backend/routes/global_chat_routes.py :: api_list_global_chats`)

### api_create_global_chat

`POST /api/global-chats` — session + CSRF. Body `{"title": "..."}`, optional. Returns the chat object, or **500** `{"error": "Failed to create global chat"}`. (`backend/routes/global_chat_routes.py :: api_create_global_chat`)

### api_get_global_chat

`GET /api/global-chats/<chat_id>` — session. Returns the chat with `messages` (including persisted `ui_payload`) and `pinned_studies`. **404** if not found. (`backend/routes/global_chat_routes.py :: api_get_global_chat`)

### api_delete_global_chat

`DELETE /api/global-chats/<chat_id>` — session + CSRF. Returns `{"ok": true}` unconditionally. (`backend/routes/global_chat_routes.py :: api_delete_global_chat`)

### api_global_chat_message_stream

`POST /api/global-chats/<chat_id>/message/stream` — session + CSRF. **SSE.**

The second streaming endpoint, and the most branch-heavy handler in the codebase. Request:

```json
{ "message": "find shotgun studies of soil", "model": "qwen3",
  "deep_search": false, "selected_studies": [],
  "report_study_id": null, "pin_study_ids": null }
```

Shares `parse_chat_stream_body` with the project stream, so the same **400** cases apply; **404** for an unknown chat.

Pinned context is built once up front (emitting a `pinned_reports` step) when pinned studies exist. The pin and report branches behave as in the project stream, differing only in scope (`SCOPE_GLOBAL`) and in passing `GLOBAL_CHAT_SYSTEM_PROMPT`.

The normal branch always delegates to `stream_agent` with `TOOL_SCHEMAS`, translating its events onto the wire: `agent_start`, `token`, `segment_tool_call {name, label, args}`, and `segment_tool_result {name, label, detail, ui_payload}`. In parallel the handler accumulates a `segments_list` — text runs are flushed into `{"type": "text", ...}` entries whenever a tool call interrupts them, and each tool segment is matched back to its result by scanning for the first not-yet-`done` segment with the same `name`. On completion the segments are frozen into the persisted `ui_payload`:

```json
{ "kind": "agent_segments", "segments": [ {"type": "text"}, {"type": "tool"} ] }
```

That matching-by-name rule is worth noting: if the same tool is called twice concurrently, results attach to the earliest open segment rather than to the specific invocation.

Agent turns re-read the pin list in `done` so the frontend can sync tool-triggered pins.

```json
{ "chat_id": "a1b2c3d4", "persisted": true, "pinned_studies": [10317] }
```

Errors surface as an `error` event with `friendly_llm_error(e, model)`. Full event payload documentation lives in `appendix-c-agent-tools-and-sse.md`.

`backend/routes/global_chat_routes.py :: api_global_chat_message_stream`

### api_pin_global_chat_study

`POST /api/global-chats/<chat_id>/pinned/<int:study_id>` — session + CSRF. **404** if the chat is not found. Returns `{"ok": true, "pinned_studies": [...]}`. (`backend/routes/global_chat_routes.py :: api_pin_global_chat_study`)

### api_unpin_global_chat_study

`DELETE /api/global-chats/<chat_id>/pinned/<int:study_id>` — session + CSRF. **404** if the chat is not found. Returns `{"ok": true, "pinned_studies": [...]}`. (`backend/routes/global_chat_routes.py :: api_unpin_global_chat_study`)

---

## Merge

### list_merge_workspaces

`GET /api/merge-workspaces` — session. Returns a JSON **array** of workspace rows for `g.user_id`, ordered by `updated_at` descending. Study slots are not included. (`backend/routes/merge_routes.py :: list_merge_workspaces`)

### create_merge_workspace

`POST /api/merge-workspaces` — session + CSRF. Body `{"name": "..."}`; blank becomes `"Untitled Merge"`. Returns the new workspace with an empty `studies` array and status **201**. (`backend/routes/merge_routes.py :: create_merge_workspace`)

### get_merge_workspace

`GET /api/merge-workspaces/<workspace_id>` — session. Returns the workspace with hydrated `studies` slots. **404** `{"error": "Not found"}` if missing or owned by another user. (`backend/routes/merge_routes.py :: get_merge_workspace`)

### delete_merge_workspace

`DELETE /api/merge-workspaces/<workspace_id>` — session + CSRF. Returns `{"deleted": "<workspace_id>"}`, or **404** when `delete_workspace` reports no rows affected. (`backend/routes/merge_routes.py :: delete_merge_workspace`)

### patch_merge_workspace

`PATCH /api/merge-workspaces/<workspace_id>` — session + CSRF. Body `{"name": "..."}`; blank → **400** `{"error": "name required"}`. Returns `{"workspace_id": ..., "name": ...}` echoing the request. `rename_workspace` returns `None` and its result is not checked, so renaming a nonexistent or unowned workspace returns 200 with no 404 — see the closing findings. (`backend/routes/merge_routes.py :: patch_merge_workspace`)

### add_study_to_merge_workspace

`POST /api/merge-workspaces/<workspace_id>/studies` — session + CSRF.

Body `{"study_id", "study_title", "data_types", "num_samples"}`. Missing `study_id` → **400**. Unknown/unowned workspace → **404**. At the 5-study cap → **400** `{"error": "Workspace already has 5 studies (maximum)"}`. Success returns `{"studies": [...]}` with status **201**. (`backend/routes/merge_routes.py :: add_study_to_merge_workspace`)

### remove_study_from_merge_workspace

`DELETE /api/merge-workspaces/<workspace_id>/studies/<int:study_id>` — session + CSRF. Returns `{"studies": [...]}` with the remaining slots, or **404** when the store returns the sentinel `"not_found"`. (`backend/routes/merge_routes.py :: remove_study_from_merge_workspace`)

### update_merge_workspace_study

`PATCH /api/merge-workspaces/<workspace_id>/studies/<int:study_id>` — session + CSRF.

Body accepts `chosen_artifact_ids` (list) or the legacy singular `chosen_artifact_id`, plus an optional `sample_filter`. The handler normalizes either form to a list before storing. Returns `{"studies": [...]}`, or **404** on the `"not_found"` sentinel. (`backend/routes/merge_routes.py :: update_merge_workspace_study`)

### validate_merge_workspace

`GET /api/merge-workspaces/<workspace_id>/validate` — session.

Pre-flight check the merge UI calls before enabling submit. No request body. **404** for an unknown workspace.

The first gate is a study-level data-type intersection via `studies_type_intersection`. With more than one study and no shared type, it short-circuits and returns a fully-formed but negative result — `compatible: false`, an explanatory string in `errors`, and an **empty** `studies` array. Clients must not assume `studies` is populated whenever the workspace has slots.

Otherwise, per slot:

1. `_resolve_artifact(slot, artifacts, common_type)` picks the explicitly chosen artifact or autopicks one within the common type.
2. True sample membership comes from `get_biom_sample_ids` reading the BIOM file (cached indefinitely). On any exception it degrades to `_get_sample_ids(sid)`, the study-level sample list — which is a **superset** of what the BIOM actually contains, so a read failure silently inflates the overlap preview.
3. The resolved artifact is copied and annotated with `num_samples` and a human-readable `reason` from `autopick_reason`.
4. An explicit `sample_filter` on the slot overrides BIOM membership; a malformed filter falls back to it.

Namespace compatibility runs with `explicit_only=True`. A merge preview (`compute_merge_preview`) is computed only when at least two studies resolved non-empty sample sets, and is `null` otherwise.

```json
{
  "compatible": true, "namespace_groups": {}, "warnings": [], "errors": [],
  "studies": [ { "study_id": 10317, "auto_artifact": {}, "chosen_artifact_ids": [] } ],
  "preview": null
}
```

Note that `validate` and `submit_merge_job` do **not** run the same check: validate passes `explicit_only=True`, submit does not. A workspace can validate clean and still be rejected at submit.

`backend/routes/merge_routes.py :: validate_merge_workspace`

### get_workspace_samples

`GET /api/merge-workspaces/<workspace_id>/samples` — session. Returns a skeleton for the sample browser: `{"studies": [{"study_id", "study_title", "total"}]}`, where `total` is the BIOM sample count. **404** for an unknown workspace. A BIOM read failure leaves `total` at `0` rather than erroring. (`backend/routes/merge_routes.py :: get_workspace_samples`)

### get_workspace_study_samples

`GET /api/merge-workspaces/<workspace_id>/studies/<int:study_id>/samples?offset=0&limit=100` — session.

Paged samples with metadata for one study, via `build_sample_page`. `offset` floors at 0; `limit` is clamped to 1–500 (default 100); non-numeric values → **400** `{"error": "Invalid offset or limit"}`. **404** for an unknown workspace, a study not in the workspace, or a study with no resolvable BIOM artifact path. (`backend/routes/merge_routes.py :: get_workspace_study_samples`)

### submit_merge_job

`POST /api/merge-workspaces/<workspace_id>/jobs` — session + CSRF.

Validates the whole workspace, snapshots it, creates a job row, and hands execution to the background executor. Returns **202**, never 200.

No meaningful request body — the job is built entirely from stored workspace state. Rejections, all **400** unless noted:

| Condition | Response |
|---|---|
| Unknown/unowned workspace | **404** `{"error": "Not found"}` |
| No studies in workspace | `{"error": "No studies in workspace"}` |
| No common data type across >1 study | `{"error": "Studies share no data type in common.", "errors": [...]}` |
| A study resolves to no BIOM artifact | `{"error": "Study <id> has no BIOM artifact"}` |
| A chosen artifact has no `full_path` | `{"error": "Study <id> artifact <aid> has no file path"}` |
| Namespace check fails | `{"error": "Workspace validation failed", "errors": [...]}` |

Artifact resolution per slot: explicit `chosen_artifact_ids` are looked up against the study's artifacts; if none of them match, it falls back to `autopick_artifact` over the type-filtered set rather than failing. Unlike `validate`, a slot may contribute **multiple** artifacts to the snapshot, but only `chosen_arts[0]` is used for the namespace compatibility check.

The snapshot written to the job is a flat list of one entry per artifact:

```json
[ { "study_id": 10317, "artifact_id": 4521,
    "artifact_path": "/path/to/otu_table.biom", "sample_ids": null } ]
```

`sample_ids` here is the raw parsed `sample_filter` — `null` when the slot had no filter — not the resolved sample list used for validation. The executor therefore re-resolves membership itself.

`create_merge_job` writes the row with status `pending`, then `run_merge_job` is submitted to `_bg_executor` with a status callback that writes back `status`, `error_message`, and `result_path`. The response is the freshly created job, before any execution:

```json
{ "job_id": "uuid", "workspace_id": "abc123", "user_id": "...",
  "status": "pending", "created_at": "...", "updated_at": "..." }
```

Poll `poll_merge_job` for progress. Merge execution runs a local `conda run` and is dev-only — see TKT-015 and `07-merge-and-biom.md`.

`backend/routes/merge_routes.py :: submit_merge_job`

### get_workspace_jobs

`GET /api/merge-workspaces/<workspace_id>/jobs` — session. Returns a JSON **array** of jobs for this workspace and user, newest first. No 404 — an unknown workspace returns `[]`. (`backend/routes/merge_routes.py :: get_workspace_jobs`)

### poll_merge_job

`GET /api/merge-jobs/<job_id>` — session. Returns the job row including `status`, `error_message`, and `result_path`. **404** if the job does not exist or belongs to another user — `get_merge_job` is owner-scoped by design so polling cannot leak another user's job. (`backend/routes/merge_routes.py :: poll_merge_job`)

---

## Artifacts

### get_artifact_samples

`GET /api/artifacts/<int:artifact_id>/samples?study_id=<id>&limit=50` — session.

Reads sample IDs from the BIOM file, then joins metadata from `qiita.sample_{study_id}` for the first `limit` of them. `limit` is capped at 500. Missing `study_id` → **400**; artifact not found in the study or lacking a file path → **404**; a BIOM read failure → **500** with the exception text. Returns a JSON **array** of `{"sample_id": "...", "fields": {...}}`, with `fields` empty for samples that have no metadata row. (`backend/routes/artifact_routes.py :: get_artifact_samples`)

### get_artifact_sample_counts

`POST /api/artifacts/sample-counts` — session + CSRF.

Body `{"study_id": 10317, "artifact_ids": [1, 2]}`; either missing or empty → **400**. Returns a bare map `{"<artifact_id>": <count>}`. Artifacts that are unknown, lack a file path, or fail to read are **omitted** rather than reported — a short response is the only signal that something failed. (`backend/routes/artifact_routes.py :: get_artifact_sample_counts`)

### download_artifact_file

`GET /api/artifacts/<int:artifact_id>/files/<int:filepath_id>/download?study_id=<id>` — session.

Streams one file from an artifact as an attachment. Missing `study_id` → **400**. `_resolve_artifact_file` locates the artifact node in the study's artifact graph (cached, else freshly fetched), finds the matching `filepath_id`, resolves the real path, and checks it is a file not under `_FORBIDDEN_ROOTS` (`/etc/`, `/proc/`, `/sys/`, `/dev/`, `/root/`).

Every failure mode raises `ValueError` and is returned as **403** — including "File not found on disk", which is a 404 condition reported as a permissions error. See the closing findings. (`backend/routes/artifact_routes.py :: download_artifact_file`)

### download_merge_result

`GET /api/merge-jobs/<job_id>/download` — session.

Sends the merge result tarball as `merge_<job_id>.tar.gz` with mimetype `application/gzip`. **404** if the job is unknown or owned by another user; **400** `{"error": "Job is <status>, not done"}` if incomplete; **404** `{"error": "Result file not found"}` if the recorded `result_path` is absent from disk. (`backend/routes/artifact_routes.py :: download_merge_result`)

---

## Aggregations

A Sample Aggregation is a user-owned, named set of **samples** grouped by study (`backend/store/aggregation_crud.py`; tables `aggregations`, `aggregation_studies`, `aggregation_samples` — see appendix B). Adding a study checks every one of its samples; the tab then unchecks or re-checks individual samples. Every mutation returns the full aggregation, and the frontend patches its state from that body rather than re-fetching:

```json
{ "aggregation_id": "…", "user_id": "…", "name": "…", "created_at": "…", "updated_at": "…",
  "file_filter": { "data_types": ["Metagenomic"], "processing": [] },
  "studies": [{ "study_id": 232, "study_title": "…", "study_abstract": "…", "data_types": "16S",
                "num_samples": 115, "num_preps": 1, "fastq_artifact_count": 1,
                "pi_name": "…", "pi_affiliation": "…", "year": 2015, "is_gold": 1,
                "selected_samples": 113, "added_at": "…" }] }
```

`num_samples` is the number of sample rows stored when the study was added (the `qiita_sample_column_names` sentinel excluded); `fastq_artifact_count` counts the study's `per_sample_FASTQ` + `FASTA` artifacts at add time; `selected_samples` is live.

`file_filter` is the aggregation's saved choice of **data types** (e.g. `Metagenomic`, `16S`) and **processing steps** — the Qiita command that produced each per-sample artifact, e.g. `Atropos v1.1.24`, `Adapter and host filtering v2023.12`, or `Raw upload` for uploads (`qiita.software_command.name` via `artifact.command_id`). An empty list means any. Processing exists because one metagenomic prep often holds the same reads two or three times (raw, adapter-trimmed, host-filtered): measured on AGP (10317), 96 of 191 metagenomic preps have 2–3 `per_sample_FASTQ` artifacts, so an unfiltered export carries ~8 files per sample. Data type covers a broader case too: a study can have samples with **no per-sample sequence file at all** — e.g. a 16S study whose only artifacts are `Demultiplexed`/`BIOM` (measured: studies 101, 1070, 1889) — and those samples still belong to a data type via **prep membership** (`qiita.prep_template_sample`, `helpers/study_samples.prep_data_types`), same as the Browse card's chips.

The filter therefore has two effects, and only data type affects the first:
1. **Scope** — which samples are in the sample table at all. A sample stays listed if no data type is chosen, or its prep membership **or** file data types intersect the chosen set (`helpers/sample_files.in_scope`). Processing never narrows scope.
2. **File counting** — `fastq`/`fasta`, files-first order, `show`, `with_files`, `select: "with_files"`, `file-facets`'s counts, and both exports all use the **full** filter (data type **and** processing). Matching is per artifact (`helpers/fastq_manifest.group_matches`), and `run_prefix` claiming is per artifact too, so filtering never changes which file a kept sample resolves to.

A sample in scope only by prep membership (no matching file) shows `fastq: null`, `fasta: false` — "—" in the UI — rather than being hidden.

### api_list_aggregations / api_create_aggregation / api_update_aggregation / api_delete_aggregation

`GET /api/aggregations` → `{"aggregations": [...]}`. `POST /api/aggregations` `{"name"}` (blank → "Untitled") → **201**. `PATCH /api/aggregations/<id>` `{"name"}` and/or `{"file_filter": {"data_types": [...], "processing": [...]}}` → the aggregation; **400** when neither key is present, on a blank name, or on a malformed filter (unknown key, a non-list, an empty or >200-char string, or >50 items per list; duplicates are dropped and lists sorted); **404** unknown/unowned. `DELETE /api/aggregations/<id>` → `{"deleted": id}`; the study and sample rows cascade.

### api_aggregation_file_facets

`GET /api/aggregations/<aggregation_id>/file-facets` — options for the tab's Data type / Processing pickers across the aggregation's studies, plus what the export would contain:

```json
{ "data_types": [{ "name": "16S", "count": 118 }, { "name": "Full Length Operon", "count": 73 },
                  { "name": "Metagenomic", "count": 7306 }],
  "processing": [{ "name": "Adapter and host filtering v2023.12", "count": 3771 }, { "name": "Raw upload", "count": 7379 }],
  "exportable": 7306, "selected": 41600 }
```

`facet_counts(file_maps, prep_maps, file_filter)` (`helpers/sample_files.py`) computes both lists, facet-style so picking one option doesn't hide the others:
- **Data type** counts are distinct samples **in scope** for that type (prep membership ∪ file, same as `in_scope` above) — `16S` above comes entirely from prep membership, since studies 101/1070 have no per-sample file. These counts **ignore the processing filter**, since scope doesn't depend on it.
- **Processing** counts are distinct samples with a matching **file**, narrowed by the data-type filter (unaffected by data types that have no file, since processing describes files).

A selected name with nothing left is still listed, at count 0, so it can be unticked. `exportable` is the number of **checked** samples with a file under the full saved filter — `0` means both exports would 404, and the tab disables them; it can be lower than a data type's scope count when that type has no file (e.g. picking `16S` alone against studies 101/1070 gives `exportable: 0`). Computes each study's availability map (`get_sample_files`, cached 6 h) and prep-membership map (`prep_data_types`, memoized 1 h) — the first call on a cold 50-study aggregation can take tens of seconds.

### api_add_study_to_aggregation

`POST /api/aggregations/<aggregation_id>/studies` — body `{"study": {study_id, study_title, study_abstract, data_types, num_samples, num_preps, pi_name, pi_affiliation, year, is_gold}}`: the Browse card's header, snapshotted so the tab renders the same card. Only `study_id` is validated. **403** if the study is not public, **404** unknown/unowned aggregation, **400** over the 50-study cap. Fetches every sample id of the study from `qiita.sample_<study_id>` (`helpers/study_samples.list_study_sample_ids`) and stores them checked. Re-adding a study already present is a no-op — it does **not** re-check samples the user unchecked.

### api_remove_study_from_aggregation

`DELETE /api/aggregations/<aggregation_id>/studies/<int:study_id>` → the aggregation; the study's `aggregation_samples` rows go with it (FK cascade).

### api_aggregation_study_samples

`GET /api/aggregations/<aggregation_id>/studies/<int:study_id>/samples?offset=0&limit=200&q=&show=all` — `limit` 1–500 (a page is one SQLite `IN (…)` bind list, kept under the 999-variable ceiling), `q` = case-insensitive substring over the sample id or any metadata value (`sample_values::text ILIKE`), `show` = `all` | `with_files` | `without_files` (**400** for anything else). Response:

```json
{ "study_id": 232, "total": 115, "offset": 0, "limit": 200, "with_files": 88,
  "columns": ["sample_type", "host_body_site", "env_material", "empo_3"], "selected_count": 113,
  "rows": [{ "sample_id": "232.…", "selected": true, "fastq": "paired", "fasta": false,
             "data_types": ["16S"], "file_data_types": ["16S"], "processing": ["Raw upload"],
             "fields": { "sample_type": "…", "…": "…" } }] }
```

Two narrowing passes, both driven by the aggregation's saved `file_filter`. First **scope**: `ids` is filtered to `helpers.sample_files.in_scope(prep_types, entries, file_filter)` — a sample stays if no data type is chosen, or its prep membership (`helpers.study_samples.prep_data_types`) or file data types intersect it; processing plays no part here. Then **file availability**: `helpers.sample_files.get_sample_files` narrowed by the **full** filter via `effective()` → `{sample_id: (fastq, fasta)}` (the map itself is per data type × processing step, see appendix B `study_sample_files_cache`); `show` filters the in-scope list by this, and it is then stably re-sorted files-first (samples with a file first, original id order preserved within each group) before the `offset`/`limit` slice — a plain SQL `LIMIT`/`OFFSET` over `qiita.sample_<id>` cannot express availability-first ordering.

Paging happens in **Python**, not SQL, for the same reason. `with_files` is the count of in-scope samples with a file **within the `q`-filtered set, before `show` narrows it** — it is the fixed "N" for the tab's "With files (N)" toggle regardless of which `show` value is currently selected. Per row, `fastq`/`fasta` are under the full filter; `data_types` is prep membership **∪** file data types, unfiltered, so the tab can show every type the sample belongs to (dimmed when the current filter excludes it); `file_data_types` is the subset that has an actual per-sample file, for solid-vs-outline chip styling — a type in `data_types` but not `file_data_types` means "this sample is in a prep of that type, but Qiita has no per-sample sequence file for it" (e.g. a 16S study with only `Demultiplexed`/`BIOM` artifacts); `processing` lists the steps of the sample's files, unfiltered. `columns` are up to four display columns picked from the study's own metadata column list (`helpers/study_samples.display_columns`, memoized per worker for an hour); the full field set of one sample is `GET /api/studies/<sid>/samples/<sample_id>`. **404** if the study is not in the aggregation; **400** on a non-integer offset/limit.

### api_set_aggregation_samples

`PATCH /api/aggregations/<aggregation_id>/studies/<int:study_id>/samples` — body either `{"add": [...], "remove": [...]}` (≤ 50,000 ids each) or `{"select": "all" | "none" | "with_files" | "matching", "q": "..."}`. `"all"` / `"none"` / `"with_files"` **replace** the whole selection (`"with_files"` checks every sample that resolves to a per-sample sequence file under the saved `file_filter` — exactly what the export can contain, optionally narrowed by `q`); `"matching"` (requires `q`) **adds** every sample the filter hits to whatever is already checked. Returns the full aggregation; **400** on any other body. Ids are stored as given — a bogus id is counted but never resolves to a file in the CSV.

### download_aggregation_csv / download_aggregation_xlsx

`GET /api/aggregations/<aggregation_id>/export.csv` — `text/csv`, `Content-Disposition: attachment; filename=aggregation_<id>_samples.csv`. Header `study_id,sample_id,file_path_in_qmounts,data_type,file_type,processing`; one row per per-sample sequence file of every checked sample, restricted to the saved `file_filter` — `per_sample_FASTQ` reads as `raw_forward_seqs` / `raw_reverse_seqs` (a paired sample is two rows) and Qiita's per-sample `FASTA` uploads as `raw_fasta`. A sample in two preps, or in two processing copies of one prep, appears once per file. Files are matched to samples by `run_prefix` over the prep's **full** sample list and only then filtered to the checked set (`helpers/fastq_manifest.build_csv_rows` — longest-prefix-first claiming needs every sample present). Samples with no resolvable file are omitted. **400** `No samples selected`; **404** when the studies have no per-sample sequence artifact matching the filter, or no checked sample resolves.

`GET /api/aggregations/<aggregation_id>/export.xlsx` — the same rows as an Excel workbook (`helpers/fastq_manifest.to_xlsx`, openpyxl write-only), `…_samples.xlsx`, bold frozen header. Every column but `study_id` is a **text** cell with number format `@`. This is the fix for "the sample id shows the study id": opened in Excel or Numbers, the CSV's `10317.000001062` parses as a number and displays as `10317`, identical to `study_id`. It replaced the earlier `?spreadsheet=1` CSV that wrote `="…"` formulas, which only read as text in apps that evaluate CSV formulas.

Plain links from the tab either way: the session cookie rides along on top-level navigation and GET carries no CSRF.

`backend/routes/aggregation_routes.py`

## Notes and observations

Behaviors that are easy to misread from the route names alone:

- **Silent-success mutations.** `api_delete_project`, `api_delete_chat`, `api_delete_global_chat`, and `patch_merge_workspace` return success regardless of whether anything was affected. Clients cannot distinguish "deleted" from "never existed".
- **Empty-list-instead-of-404.** `get_workspace_jobs` returns an empty collection for an unknown parent.
- **Settings are global, not per-user.** `api_get_settings` / `api_post_settings` read and write the shared `meta` table through `get_setting` / `set_setting`, which take no `user_id`. Any authenticated user reads the same flag and overwrites the same stored Anthropic key.
- **Artifact downloads are not study-scoped.** `download_artifact_file` requires a session but performs no `is_study_public` check (unlike `api_study_detail`) and no ownership check. Its path safety is a blocklist of forbidden roots rather than an allowlist of permitted ones.
- **Validate and submit disagree.** `validate_merge_workspace` runs `check_namespace_compatibility(..., explicit_only=True)`; `submit_merge_job` runs it without that flag. A green validate does not guarantee a successful submit.
- **`str(e)` leaks into responses.** `api_study_detail`, `search`, `api_first_studies`, `api_sample_detail`, and `get_artifact_samples` return raw exception text in 500 bodies.

---

## See also

- [`02-authentication.md`](02-authentication.md) — session model, single-login PAT handling, and the CSRF contract
- [`06-streaming-and-chat.md`](06-streaming-and-chat.md) — SSE contract and the frontend segments model
- [`07-merge-and-biom.md`](07-merge-and-biom.md) — merge workspace lifecycle, BIOM resolution, and job execution
- [`appendix-c-agent-tools-and-sse.md`](appendix-c-agent-tools-and-sse.md) — agent tool definitions and full SSE event payloads
