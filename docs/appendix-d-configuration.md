# Appendix D — Configuration

*Every environment variable, tunable constant, and system prompt that changes QiitaExplore's behavior, with its real default and where it is read.*

---

## Read this first: configuration comes from a `.env` **file**, not the shell

`backend/config.py` calls `load_dotenv()` at module import, before any variable is read. python-dotenv resolves the file by walking up from the directory containing `config.py` — so the file it finds is **`qiita_explore/.env`**.

This matters more than it looks. The backend runs under gunicorn as a service (`qiita_explore/start_barnacle.sh`), and a service inherits essentially nothing from the shell you were sitting in when you configured something. Exporting a variable in your terminal, or putting it in `~/.bashrc`, has no effect on the running backend. Two consequences:

- **To change a setting, edit `qiita_explore/.env` and restart gunicorn.** There is one exception, documented below (`ANTHROPIC_API_KEY`).
- **The only variables that legitimately live in the shell are the ones the start scripts export themselves** — `QIITA_CONFIG_FP` and `QIITA_EXPERIMENT_DB_PATH` in `start_barnacle.sh`, plus `AGENT_DEBUG` / `HARNESS_LOG_FP` in `run_agent_harness.sh`. Everything else belongs in the `.env` file. (A shell export of a `.env` name still wins, because `load_dotenv()` does not override variables already present in the environment — but relying on that is how configuration drift starts.)

Because `config.py` reads every variable at import time, **the restart column below is "yes" for every variable except one**. There is no reload endpoint and no file watcher.

---

## Environment variables

Each variable is anchored for cross-referencing: `#env-<NAME>`.

### LLM / provider

<a id="env-OPENAI_API_KEY"></a>
<a id="env-API_KEY"></a>
<a id="env-ANTHROPIC_API_KEY"></a>

| Name | Default | Consumed by | Effect | Restart? |
|---|---|---|---|---|
| `OPENAI_API_KEY` | *(none)* | `backend/config.py` (module level) | API key for the NRP-Nautilus OpenAI-compatible endpoint (`https://ellm.nrp-nautilus.io/v1`). Checked first. | Yes |
| `API_KEY` | *(none)* | `backend/config.py` (module level) | Fallback for the same NRP key — `API_KEY` is used only if `OPENAI_API_KEY` is unset. The local `.env` uses this name. | Yes |
| `ANTHROPIC_API_KEY` | `""` | `backend/config.py` (module level, and `backend/config.py :: get_client`) | API key for the three `claude-*` models. **The only runtime-settable value in the system** — see below. | **No** |

**The `ANTHROPIC_API_KEY` exception.** `backend/config.py :: get_client` returns `(client, provider)`. For a model whose metadata says `provider: "anthropic"`, it does *not* reuse the import-time client. It re-reads the key on every call:

```python
if provider == "anthropic":
    from store.crud import get_setting
    key = get_setting('anthropic_api_key') or ANTHROPIC_API_KEY
    return _anthropic.Anthropic(api_key=key, timeout=300.0), "anthropic"
```

`get_setting` reads the SQLite `meta` table (`backend/store/crud.py :: get_setting`), which `POST /api/settings` writes (`backend/routes/study_routes.py :: api_post_settings`). So an operator can paste a new Anthropic key into the UI and the next request picks it up — no restart, and the DB value takes precedence over the env var. Every other variable on this page is bound to a module-level name at import and requires a process restart.

Both provider clients are constructed with a **300-second timeout**, hardcoded in `backend/config.py`.

### Authentication

<a id="env-QIITA_EXPLORE_PAT_ENCRYPTION_KEY"></a>
<a id="env-AUTH_PAT_REVERIFY_INTERVAL_SECONDS"></a>
<a id="env-AUTH_SESSION_ABSOLUTE_TTL_SECONDS"></a>
<a id="env-AUTH_SESSION_IDLE_TTL_SECONDS"></a>
<a id="env-QIITA_EXPLORE_COOKIE_SECURE"></a>
<a id="env-QIITA_EXPLORE_ALLOWED_ORIGINS"></a>
<a id="env-QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX"></a>

| Name | Default | Consumed by | Effect | Restart? |
|---|---|---|---|---|
| `QIITA_EXPLORE_PAT_ENCRYPTION_KEY` | **none — no fallback** | `backend/config.py` → `backend/helpers/pat_crypto.py :: _get_fernet` | Fernet key encrypting PATs at rest in `auth_sessions.pat_encrypted`. **Required.** | Yes |
| `AUTH_PAT_REVERIFY_INTERVAL_SECONDS` | `900` (computed as `15 * 60`) | `backend/helpers/auth_middleware.py` | How stale a session's `last_verified_at` may get before the middleware re-calls Qiita `whoami` to confirm the PAT is still valid. | Yes |
| `AUTH_SESSION_ABSOLUTE_TTL_SECONDS` | `2592000` (computed as `30 * 24 * 3600`) | `backend/store/auth_store.py`, `backend/routes/auth_routes.py` | Hard session lifetime; also the session cookie's `max_age`. | Yes |
| `AUTH_SESSION_IDLE_TTL_SECONDS` | `604800` (computed as `7 * 24 * 3600`) | `backend/store/auth_store.py` | Idle expiry — a session unused for this long is dead even if inside the absolute TTL. | Yes |
| `QIITA_EXPLORE_COOKIE_SECURE` | `true` | `backend/routes/auth_routes.py :: _cookie_kwargs` | `Secure` flag on the session cookie. Anything in `false` / `0` / `no` (case-insensitive) disables it. | Yes |
| `QIITA_EXPLORE_ALLOWED_ORIGINS` | `""` → empty list | `backend/run.py` (Flask-CORS), `backend/routes/auth_routes.py` | Comma-separated **exact** origins allowed to make credentialed cross-origin requests. Empty means no CORS, which is correct for a same-origin deployment behind nginx. | Yes |
| `QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX` | unset → `None` | `backend/store/legacy_claim.py` | The one Qiita `principal_idx` permitted to claim legacy `'default'`-owned data via `POST /api/auth/claim-default`. Unset disables claiming entirely. Non-numeric values are silently treated as unset (`_claimant_raw.isdigit()` guard). | Yes |

The session cookie name itself, `qe_sid`, is **not** configurable — it is the literal `SESSION_COOKIE_NAME` in `backend/config.py`.

On `QIITA_EXPLORE_PAT_ENCRYPTION_KEY`: there is deliberately no development fallback and no auto-generated key. `backend/helpers/pat_crypto.py :: _get_fernet` raises `PatCryptoError` with generation instructions when the value is missing, and raises again if the value is not a valid Fernet key. Note the precise failure timing: **this raises on first PAT encrypt/decrypt — the first login or session verification — not at process start.** The process will boot happily and then fail every auth operation. Treat a missing key as a boot-blocking misconfiguration even though the process does not refuse to start. Generate one with:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Qiita connectivity

<a id="env-QIITA_CONTROL_PLANE_URL"></a>
<a id="env-QIITA_PUBLIC_LOGIN_URL"></a>
<a id="env-QIITA_LOGINROCKET_URL"></a>
<a id="env-QIITA_WHOAMI_TIMEOUT_SECONDS"></a>
<a id="env-QIITA_CONFIG_FP"></a>
<a id="env-QIITA_BASE_DATA_DIR"></a>

| Name | Default | Consumed by | Effect | Restart? |
|---|---|---|---|---|
| `QIITA_CONTROL_PLANE_URL` | `http://127.0.0.1:8080` | `backend/helpers/qiita_client.py :: whoami` | Base URL for **backend-to-Qiita** calls (identity only). Trailing slash stripped. | Yes |
| `QIITA_PUBLIC_LOGIN_URL` | *computed* — falls back to `QIITA_CONTROL_PLANE_URL` | `backend/routes/auth_routes.py` | Base URL the user's **browser** is redirected to for login. Separate from the above because in a split-tunnel deployment (backend reaches Qiita over a reverse SSH tunnel, browser reaches it directly) the two addresses differ. | Yes |
| `QIITA_LOGINROCKET_URL` | `""` → disabled | `backend/routes/auth_routes.py` | When set, `/api/auth/login-url` wraps the control-plane login in a LoginRocket `/logout?redirect_uri=…` so a cached AuthRocket session cannot hijack login. **Leave unset** — see traps. | Yes |
| `QIITA_WHOAMI_TIMEOUT_SECONDS` | `5` (parsed as float) | `backend/helpers/qiita_client.py :: whoami` | httpx timeout for the whoami call. Timeouts are classified `transient_error=True` and do **not** destroy the local session. | Yes |
| `QIITA_CONFIG_FP` | *(none)* | vendored `qiita_core/configuration_manager.py` | Path to the Qiita config file supplying **PostgreSQL credentials** (user, password, database, host, port). Read via `environ["QIITA_CONFIG_FP"]` — a hard `KeyError` if absent. Not read by `config.py`; exported by both start scripts. | Yes |
| `QIITA_BASE_DATA_DIR` | `""` | `backend/helpers/qiita_fetch.py`, `backend/helpers/artifact_graph.py`, `backend/helpers/biom_samples.py` | Prefix prepended to **relative** artifact filepaths from the Qiita DB. Empty means paths are used as-is — fine when the DB stores absolute paths, broken when it does not. | Yes |

`QIITA_CONFIG_FP` is the one variable that is hardcoded to an absolute path in checked-in scripts (`/home/d4sharma/qiita-web/qiita_config.cfg` in both `start_barnacle.sh` and `run_agent_harness.sh`). It is machine-specific and will not resolve on another host.

### Storage & pooling

<a id="env-QIITA_EXPERIMENT_DB_PATH"></a>
<a id="env-PG_POOL_MIN_CONN"></a>
<a id="env-PG_POOL_MAX_CONN"></a>

| Name | Default | Consumed by | Effect | Restart? |
|---|---|---|---|---|
| `QIITA_EXPERIMENT_DB_PATH` | *computed* — `backend/data/projects.db` | `backend/store/db.py` | Path to the local SQLite store (projects, chats, messages, sessions, caches). `start_barnacle.sh` overrides the default to `$HOME/.qiita-experiment/projects.db` and `mkdir -p`s the parent. | Yes |
| `PG_POOL_MIN_CONN` | `2` | `backend/helpers/pg_pool.py` | Minimum connections in the read-only PostgreSQL pool. | Yes |
| `PG_POOL_MAX_CONN` | `8` | `backend/helpers/pg_pool.py` | Maximum connections in that pool. This is a per-gunicorn-worker pool; multiply by the worker count for real database load. | Yes |

The `backend/data/` default directory is created at import time by `backend/store/db.py`, so the fallback path always exists even when the variable points elsewhere.

### Search tuning

<a id="env-GLOBAL_SEARCH_SQL_LIMIT_BROAD"></a>
<a id="env-GLOBAL_SEARCH_SQL_LIMIT_NARROW"></a>
<a id="env-SAMPLE_SEARCH_DEFAULT_CANDIDATES"></a>
<a id="env-SAMPLE_SEARCH_DEEP_CANDIDATES"></a>
<a id="env-SAMPLE_SEARCH_PROBE_TIMEOUT_MS"></a>

| Name | Default | Consumed by | Effect | Restart? |
|---|---|---|---|---|
| `GLOBAL_SEARCH_SQL_LIMIT_BROAD` | `120` | `backend/services/study_service.py`, `backend/services/llm.py` | Row limit for broad text searches. `services/llm.py` clamps the effective value to `1…150`. | Yes |
| `GLOBAL_SEARCH_SQL_LIMIT_NARROW` | `50` | `backend/services/llm.py` | Row limit for narrow/targeted searches. Same `1…150` clamp. | Yes |
| `SAMPLE_SEARCH_DEFAULT_CANDIDATES` | `40` | `backend/helpers/agent_tools.py` | How many studies the per-sample metadata probe fans out across in normal mode. | Yes |
| `SAMPLE_SEARCH_DEEP_CANDIDATES` | `500` | `backend/helpers/agent_tools.py`, `backend/routes/study_routes.py` | Same, in deep-search mode. Selected by the `deep_search` flag. | Yes |
| `SAMPLE_SEARCH_PROBE_TIMEOUT_MS` | `8000` | `backend/helpers/sample_search.py` | Per-connection PostgreSQL `statement_timeout`, passed as `options=f"-c statement_timeout={…}"` on the probe pool. A probe that exceeds it is killed by the server, not the client. | Yes |

Sample search is bounded on three independent axes, and all three must be understood together: candidate count (above), keywords per probe (`_MAX_KEYWORDS_PER_PROBE`, non-env), and statement timeout (above). See the non-env table.

### Context budgeting

<a id="env-PINNED_REPORT_CONTEXT_MAX_CHARS"></a>
<a id="env-PINNED_REPORT_MIN_PER_STUDY"></a>
<a id="env-PROJECT_SUMMARY_GEN_LIMIT"></a>

| Name | Default | Consumed by | Effect | Restart? |
|---|---|---|---|---|
| `PINNED_REPORT_CONTEXT_MAX_CHARS` | `40000` | `backend/helpers/qiita_fetch.py` | Total character ceiling for the "PINNED STUDY REPORTS" block injected into chat context. The assembled text is truncated to this. | Yes |
| `PINNED_REPORT_MIN_PER_STUDY` | `2000` | `backend/helpers/qiita_fetch.py` | Floor on each pinned study's share. The per-study allowance is `max(MIN_PER_STUDY, MAX_CHARS // len(study_ids))`, so with many pins the floor wins and the total can exceed the ceiling before the final truncate. | Yes |
| `PROJECT_SUMMARY_GEN_LIMIT` | `5` | **nothing** | Defined in `backend/config.py` and imported nowhere. Dead configuration. | n/a |

Note that the overall LLM context budget is **not** an environment variable. `backend/config.py :: context_budget_chars` derives it from the selected model's context window — see the model roster.

### Merge

<a id="env-MERGE_CONDA_ENV"></a>
<a id="env-MERGE_RESULTS_DIR"></a>

| Name | Default | Consumed by | Effect | Restart? |
|---|---|---|---|---|
| `MERGE_CONDA_ENV` | `qiita` | `backend/helpers/merge_executor.py` | Conda environment name passed to `conda run -n <env>` for the merge subprocess. | Yes |
| `MERGE_RESULTS_DIR` | *computed* — `backend/data/merge_results` | `backend/helpers/merge_executor.py`, `backend/routes/merge_routes.py` | Where merge job working directories and the final `{job_id}.tar.gz` land. Created with `os.makedirs(..., exist_ok=True)` at import. | Yes |

The merge executor is explicitly dev-only. Its module docstring carries a `TODO (before merging to master)` to replace the local `subprocess` path with an SFTP+SSH pipeline. Both variables above configure a code path that will not work on a remote deployment regardless of their values.

### Debug / dev / harness

<a id="env-QIITA_EXPLORE_DEBUG_ERRORS"></a>
<a id="env-AGENT_DEBUG"></a>
<a id="env-HARNESS_LOG_FP"></a>
<a id="env-HARNESS_TEXT_PREVIEW"></a>
<a id="env-BARNACLE_URL"></a>

| Name | Default | Consumed by | Effect | Restart? |
|---|---|---|---|---|
| `QIITA_EXPLORE_DEBUG_ERRORS` | `false` | `backend/routes/auth_routes.py` | When truthy (`1` / `true` / `yes`), an unexpected exception in `POST /auth/connect` returns its type and message **in the JSON response body**, not only the server log. | Yes |
| `AGENT_DEBUG` | unset | `backend/agent_harness.py` | Any truthy value sets the harness log level to `DEBUG` instead of `INFO`. Exported unconditionally by `run_agent_harness.sh`. Harness only — the gunicorn backend ignores it. | n/a (CLI) |
| `HARNESS_LOG_FP` | unset | `backend/agent_harness.py` | File the harness tees ANSI-stripped stdout into. `run_agent_harness.sh` sets it to `logs/harness_<timestamp>.log`. | n/a (CLI) |
| `HARNESS_TEXT_PREVIEW` | `2000` | `backend/agent_harness.py` | Characters of each tool result the harness prints. | n/a (CLI) |
| `BARNACLE_URL` | `http://localhost:5001` | `backend/tests/e2e/conftest.py` | Base URL the end-to-end test suite points at. Test-only. | n/a (tests) |

`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `API_KEY` are additionally read directly from `os.environ` by `backend/tests/e2e/parity_helpers.py`, bypassing `config.py`. `QIITA_EXPERIMENT_DB_PATH` is *written* by `backend/tests/test_auth.py` to redirect the suite at a temporary database.

### pi sidecar

<a id="env-PI_SIDECAR_URL"></a>
<a id="env-PI_SIDECAR_SECRET"></a>
<a id="env-PI_ALLOWED_TOOL_CALLERS"></a>
<a id="env-PI_SCOPE_TOKEN_KEY"></a>
<a id="env-PI_SCOPE_TOKEN_TTL_SECONDS"></a>
<a id="env-PI_BACKEND_GLOBAL"></a>
<a id="env-PI_BACKEND_PROJECT"></a>

The pi-backed agent runtime (`qiita_explore/pi_sidecar/`, a standalone Node process — see [`05-agent.md`](05-agent.md#pi-backend-2026-behind-feature-flags)). All seven read by `backend/config.py`, same restart rules as everything else on this page.

In the deployed topology the sidecar runs on the **intermediate node** and Flask on **barnacle**, so `/api/internal/tools/*` is a real cross-machine boundary, not a loopback convenience. It has three independent gates — source IP, shared secret, scope token — so that no single leaked value is sufficient. The localhost defaults below are for local development only.

| Name | Default | Consumed by | Effect | Restart? |
|---|---|---|---|---|
| `PI_SIDECAR_URL` | `http://127.0.0.1:5100` | `backend/helpers/pi_client.py` | Base URL of the pi sidecar HTTP service. The localhost default is a dev convenience — set it explicitly to the intermediate node in deployment. | Yes |
| `PI_SIDECAR_SECRET` | *(none)* | `backend/helpers/pi_client.py`, `backend/routes/internal_tool_routes.py`, sidecar's own `PI_SIDECAR_SECRET` env | Shared secret the sidecar presents (`X-Pi-Secret`) on **every** internal tool request — schema reads *and* tool calls. Must be set identically on both processes. No insecure fallback: unset fails closed and 401s everything. | Yes (both processes) |
| `PI_ALLOWED_TOOL_CALLERS` | *(empty — no IP restriction)* | `backend/routes/internal_tool_routes.py` | Comma-separated exact remote addresses permitted to call `/api/internal/tools/*` (the intermediate node). Empty is correct for local dev; **always set it in deployment** so a leaked `PI_SIDECAR_SECRET` is not usable from an arbitrary host. | Yes |
| `PI_SCOPE_TOKEN_KEY` | *(none)* | `backend/helpers/scope_token.py` | HMAC key signing the short-lived per-turn scope token that authorizes a tool call's `POST /api/internal/tools/<name>`. This — not the sidecar, not `PI_SIDECAR_SECRET` — is what makes project-chat scoping a hard boundary. No insecure fallback; `mint_scope_token`/`verify_scope_token` raise if unset. | Yes |
| `PI_SCOPE_TOKEN_TTL_SECONDS` | `600` | `backend/routes/global_chat_routes.py`, `backend/routes/chat_routes.py` | How long a minted scope token is valid — long enough to cover one slow, tool-heavy turn, short enough that a leaked token is useless well before the next one is minted. | Yes |
| `PI_BACKEND_GLOBAL` | **`true`** | `backend/routes/global_chat_routes.py` | pi is the default runtime. Set `false` to revert global chat to the in-process `stream_agent` loop — a rollback needing no deploy. Independent of `PI_BACKEND_PROJECT`. | Yes |
| `PI_BACKEND_PROJECT` | **`true`** | `backend/routes/chat_routes.py` | pi is the default runtime. Project chat is agentic, with every tool call hard-scoped to the project (`backend/helpers/project_scope.py`). Set `false` to revert to the non-agentic, context-stuffed path. | Yes |

The sidecar itself is a separate Node process, started independently (`qiita_explore/pi_sidecar/start_sidecar.sh`), with its own env vars — `PI_SIDECAR_PORT` (default `5100`), `PI_SIDECAR_HOST` (default `127.0.0.1`), `FLASK_INTERNAL_URL` (default `http://127.0.0.1:5001`), `PI_SIDECAR_STATE_DIR` (default `pi_sidecar/.state`), plus `OPENAI_API_KEY`/`API_KEY` (reused for the NRP provider) and `ANTHROPIC_API_KEY` (picked up by pi's built-in Anthropic provider). It refuses to start without `PI_SIDECAR_SECRET`, without Node ≥ 22, or without `node_modules`.

**Because both `PI_BACKEND_*` now default to true, `PI_SIDECAR_SECRET` and `PI_SCOPE_TOKEN_KEY` are effectively required.** `backend/config.py :: pi_config_errors()` checks them (plus a `PI_SIDECAR_PORT` set without a matching `PI_SIDECAR_URL`) and `run.py` **refuses to boot** if any are missing. Previously the backend would start fine and every chat turn would 401 — which reads as "chat is broken", not "one variable is unset". To run without the sidecar at all, use `PI_SKIP_SIDECAR=1 bash qiita_explore/start_barnacle.sh`: that exports both flags `false` for the run, so "Flask alone" means the legacy chat path rather than a backend that 500s on every message.

**Deploying across two hosts** means changing four of these together, and getting a subset right leaves you with a sidecar Flask cannot reach or a tool route the sidecar cannot call:

| On | Set | To |
|---|---|---|
| intermediate node | `PI_SIDECAR_HOST` | `0.0.0.0` — the loopback default makes the sidecar unreachable from barnacle |
| intermediate node | `FLASK_INTERNAL_URL` | barnacle's internal base URL |
| barnacle | `PI_SIDECAR_URL` | the intermediate node's `host:5100` |
| barnacle | `PI_ALLOWED_TOOL_CALLERS` | the intermediate node's address |

Firewall both directions to just those two hosts: the sidecar's port is otherwise an unauthenticated LLM endpoint to anything that can route to it.

### Minimum viable `.env`

The smallest file that produces a backend which boots, queries, authenticates, and answers:

```
API_KEY=<nrp-nautilus-key>
QIITA_EXPLORE_PAT_ENCRYPTION_KEY=<fernet-key>
QIITA_CONTROL_PLANE_URL=https://<qiita-control-plane>
QIITA_PUBLIC_LOGIN_URL=https://<browser-reachable-qiita>
QIITA_BASE_DATA_DIR=/path/to/qiita/data
```

Plus `QIITA_CONFIG_FP` and `QIITA_EXPERIMENT_DB_PATH` exported by `start_barnacle.sh`. Everything else has a default that is at least defensible.

For comparison, the `.env` currently checked in at `qiita_explore/.env` sets four keys: `DIRECTORY` (read by nothing in the backend), `API_KEY`, `QIITA_CONTROL_PLANE_URL`, and `QIITA_PUBLIC_LOGIN_URL`. It does **not** set `QIITA_EXPLORE_PAT_ENCRYPTION_KEY` — a backend started from it will fail on first login, in the deferred way described above.

### Alphabetical index

| Variable | Group | Default |
|---|---|---|
| [`AGENT_DEBUG`](#env-AGENT_DEBUG) | Debug/harness | unset |
| [`ANTHROPIC_API_KEY`](#env-ANTHROPIC_API_KEY) | LLM | `""` |
| [`API_KEY`](#env-API_KEY) | LLM | *(none)* |
| [`AUTH_PAT_REVERIFY_INTERVAL_SECONDS`](#env-AUTH_PAT_REVERIFY_INTERVAL_SECONDS) | Auth | `900` |
| [`AUTH_SESSION_ABSOLUTE_TTL_SECONDS`](#env-AUTH_SESSION_ABSOLUTE_TTL_SECONDS) | Auth | `2592000` |
| [`AUTH_SESSION_IDLE_TTL_SECONDS`](#env-AUTH_SESSION_IDLE_TTL_SECONDS) | Auth | `604800` |
| [`BARNACLE_URL`](#env-BARNACLE_URL) | Tests | `http://localhost:5001` |
| [`GLOBAL_SEARCH_SQL_LIMIT_BROAD`](#env-GLOBAL_SEARCH_SQL_LIMIT_BROAD) | Search | `120` |
| [`GLOBAL_SEARCH_SQL_LIMIT_NARROW`](#env-GLOBAL_SEARCH_SQL_LIMIT_NARROW) | Search | `50` |
| [`HARNESS_LOG_FP`](#env-HARNESS_LOG_FP) | Debug/harness | unset |
| [`HARNESS_TEXT_PREVIEW`](#env-HARNESS_TEXT_PREVIEW) | Debug/harness | `2000` |
| [`MERGE_CONDA_ENV`](#env-MERGE_CONDA_ENV) | Merge | `qiita` |
| [`MERGE_RESULTS_DIR`](#env-MERGE_RESULTS_DIR) | Merge | computed |
| [`OPENAI_API_KEY`](#env-OPENAI_API_KEY) | LLM | *(none)* |
| [`PG_POOL_MAX_CONN`](#env-PG_POOL_MAX_CONN) | Storage | `8` |
| [`PG_POOL_MIN_CONN`](#env-PG_POOL_MIN_CONN) | Storage | `2` |
| [`PI_ALLOWED_TOOL_CALLERS`](#env-PI_ALLOWED_TOOL_CALLERS) | pi sidecar | *(empty — no IP restriction)* |
| [`PI_BACKEND_GLOBAL`](#env-PI_BACKEND_GLOBAL) | pi sidecar | `true` |
| [`PI_BACKEND_PROJECT`](#env-PI_BACKEND_PROJECT) | pi sidecar | `true` |
| [`PI_SCOPE_TOKEN_KEY`](#env-PI_SCOPE_TOKEN_KEY) | pi sidecar | **required if either PI_BACKEND_\* is true** |
| [`PI_SCOPE_TOKEN_TTL_SECONDS`](#env-PI_SCOPE_TOKEN_TTL_SECONDS) | pi sidecar | `600` |
| [`PI_SIDECAR_SECRET`](#env-PI_SIDECAR_SECRET) | pi sidecar | **required if either PI_BACKEND_\* is true** |
| [`PI_SIDECAR_URL`](#env-PI_SIDECAR_URL) | pi sidecar | `http://127.0.0.1:5100` |
| [`PINNED_REPORT_CONTEXT_MAX_CHARS`](#env-PINNED_REPORT_CONTEXT_MAX_CHARS) | Context | `40000` |
| [`PINNED_REPORT_MIN_PER_STUDY`](#env-PINNED_REPORT_MIN_PER_STUDY) | Context | `2000` |
| [`PROJECT_SUMMARY_GEN_LIMIT`](#env-PROJECT_SUMMARY_GEN_LIMIT) | Context (dead) | `5` |
| [`QIITA_BASE_DATA_DIR`](#env-QIITA_BASE_DATA_DIR) | Qiita | `""` |
| [`QIITA_CONFIG_FP`](#env-QIITA_CONFIG_FP) | Qiita | *(none)* |
| [`QIITA_CONTROL_PLANE_URL`](#env-QIITA_CONTROL_PLANE_URL) | Qiita | `http://127.0.0.1:8080` |
| [`QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX`](#env-QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX) | Auth | unset |
| [`QIITA_EXPERIMENT_DB_PATH`](#env-QIITA_EXPERIMENT_DB_PATH) | Storage | computed |
| [`QIITA_EXPLORE_ALLOWED_ORIGINS`](#env-QIITA_EXPLORE_ALLOWED_ORIGINS) | Auth | `""` |
| [`QIITA_EXPLORE_COOKIE_SECURE`](#env-QIITA_EXPLORE_COOKIE_SECURE) | Auth | `true` |
| [`QIITA_EXPLORE_DEBUG_ERRORS`](#env-QIITA_EXPLORE_DEBUG_ERRORS) | Debug | `false` |
| [`QIITA_EXPLORE_PAT_ENCRYPTION_KEY`](#env-QIITA_EXPLORE_PAT_ENCRYPTION_KEY) | Auth | **required** |
| [`QIITA_LOGINROCKET_URL`](#env-QIITA_LOGINROCKET_URL) | Qiita | `""` (keep unset) |
| [`QIITA_PUBLIC_LOGIN_URL`](#env-QIITA_PUBLIC_LOGIN_URL) | Qiita | computed |
| [`QIITA_WHOAMI_TIMEOUT_SECONDS`](#env-QIITA_WHOAMI_TIMEOUT_SECONDS) | Qiita | `5` |
| [`SAMPLE_SEARCH_DEEP_CANDIDATES`](#env-SAMPLE_SEARCH_DEEP_CANDIDATES) | Search | `500` |
| [`SAMPLE_SEARCH_DEFAULT_CANDIDATES`](#env-SAMPLE_SEARCH_DEFAULT_CANDIDATES) | Search | `40` |
| [`SAMPLE_SEARCH_PROBE_TIMEOUT_MS`](#env-SAMPLE_SEARCH_PROBE_TIMEOUT_MS) | Search | `8000` |

Forty names in total: thirty-eight read by `config.py` or backend helpers (including the six `PI_*` pi-sidecar variables), plus `QIITA_CONFIG_FP` (vendored `qiita_core`) and `BARNACLE_URL` (tests only).

---

## Unsafe defaults and traps

**Must be set for a healthy production boot:**

1. **[`QIITA_EXPLORE_PAT_ENCRYPTION_KEY`](#env-QIITA_EXPLORE_PAT_ENCRYPTION_KEY)** — no fallback, and the failure is deferred to first login rather than to startup. A backend with this unset looks healthy and cannot authenticate anyone. It is absent from the checked-in `.env`.
2. **[`QIITA_CONFIG_FP`](#env-QIITA_CONFIG_FP)** — without it the vendored config manager raises `KeyError`, and there is no database. The value baked into both start scripts is a hardcoded path on one specific machine.
3. **[`OPENAI_API_KEY`](#env-OPENAI_API_KEY) or [`API_KEY`](#env-API_KEY)** — the OpenAI client is constructed with `api_key=None` if neither is set. Every NRP model call fails at request time.

**Defaults that are wrong for production:**

| Variable | Default | Why it is wrong outside local dev |
|---|---|---|
| [`QIITA_CONTROL_PLANE_URL`](#env-QIITA_CONTROL_PLANE_URL) | `http://127.0.0.1:8080` | Loopback, and it collides with the port nginx listens on in `nginx.conf`. Any real deployment must set it. |
| [`QIITA_EXPLORE_DEBUG_ERRORS`](#env-QIITA_EXPLORE_DEBUG_ERRORS) | `false` (safe) | The default is correct; the trap is leaving it **on** after debugging. It leaks exception internals to unauthenticated callers of `/auth/connect`. |
| [`QIITA_BASE_DATA_DIR`](#env-QIITA_BASE_DATA_DIR) | `""` | Silently produces relative artifact paths that resolve against the gunicorn working directory. Fails as "file not found" far from the cause. |
| [`MERGE_CONDA_ENV`](#env-MERGE_CONDA_ENV) | `qiita` | Names a *local* conda env; the whole merge executor is dev-only by its own TODO. |
| [`PG_POOL_MAX_CONN`](#env-PG_POOL_MAX_CONN) | `8` | Per gunicorn worker. `start_barnacle.sh` runs 4 workers, so the real ceiling against Qiita's PostgreSQL is 32 connections, plus a separate short-lived `ThreadedConnectionPool` per sample-search call. |
| [`QIITA_EXPLORE_COOKIE_SECURE`](#env-QIITA_EXPLORE_COOKIE_SECURE) | `true` (safe) | Correct by default. The trap is setting it `false` for loopback dev and shipping that `.env`. |

**The `QIITA_LOGINROCKET_URL` trap.** Leave it **unset**. The variable exists to route login through a LoginRocket `/logout` first, defeating a cached AuthRocket session that would otherwise complete login as the previously-signed-in user. But the logout-first wrap does not work when pointed at an external control-plane URL, and the barnacle environment setup actively deletes the variable rather than passing it through. Setting it in `.env` produces a login flow that appears configured and misbehaves. The code path is real and tested (`backend/tests/test_auth.py`); the deployment topology is what breaks it.

---

## Model roster

Eleven models in `ALLOWED_MODELS`, each with an entry in `MODEL_METADATA` (`backend/config.py`). `DEFAULT_MODEL` is `gemma`. The budget column is derived, not stored — `backend/config.py :: context_budget_chars` computes `max(8000, int((context_tokens - 8000) * 3.5))`. The `8000` reserve and the `3.5` chars-per-token ratio are both literals in that function.

| Name | Provider | Tier | Size | Context (tokens) | Modalities | `supports_tools` | Budget (chars) |
|---|---|---|---|---|---|---|---|
| `qwen3` | nrp | main | 397B | 1,010,000 | image, video | yes | 3,507,000 |
| `qwen3-small` | nrp | main | 27B | 1,010,000 | image, video | yes | 3,507,000 |
| `gpt-oss` | nrp | main | 120B | 131,072 | — | yes | 430,752 |
| `gemma` *(default)* | nrp | main | 31B | 262,144 | image, video | yes | 889,504 |
| `gemma-small` | nrp | evaluating | 12B | 262,144 | image, video, audio | yes | 889,504 |
| `kimi` | nrp | evaluating | 1T | 262,144 | image, video | yes | 889,504 |
| `glm-5` | nrp | evaluating | 744B | 524,288 | — | yes | 1,807,008 |
| `minimax-m2` | nrp | evaluating | 230B | 204,800 | — | yes | 688,800 |
| `claude-haiku-4-5` | anthropic | main | — | 200,000 | image | yes | 672,000 |
| `claude-sonnet-4-6` | anthropic | main | — | 200,000 | image | yes | 672,000 |
| `claude-opus-4-8` | anthropic | evaluating | — | 200,000 | image | yes | 672,000 |

**`supports_tools` is a routing decision, not a capability note** — but as of the pi cutover every configured model returns `True`, so it no longer routes anything. `backend/routes/global_chat_routes.py` still branches on `model_supports_tools(model)`, and `/api/internal/models` filters the pi roster on it, so a future model that cannot emit streaming tool calls must still set it `False`. The branch it used to select — an LLM-planned query followed by keyword search — has been deleted; there is no third path any more.

`gemma-small` was the one model with `supports_tools: False`, on the basis that it could not emit streaming tool calls. Checked against the live NRP endpoint and corrected: it can. Its size and context window were wrong in the same row (`~8B` / 131,072; actually 12B / 262,144), as was `glm-5`'s context (202,752; actually 524,288 — pi compacts at `contextWindow − 16384`, so glm-5 was truncating at ~186k of a real ~508k window).

`provider` splits along a second axis. On the pi path it decides how the sidecar registers the model: `provider: "nrp"` models are the ones `/api/internal/models` serves, registered against the NRP OpenAI-compatible endpoint, while `anthropic` models pass through to pi's own built-in provider (which reads `ANTHROPIC_API_KEY` from the sidecar's environment). On the legacy path `backend/config.py :: get_client` returns the shared NRP `OpenAI` client or a freshly constructed `anthropic.Anthropic`, and the agent loop has two separate implementations behind one signature — `_stream_anthropic_agent` versus the inline OpenAI loop in `backend/helpers/agent.py :: stream_agent`.

The roster is hand-maintained by decision. `test_context_windows_match_config_exactly` proves Flask and the sidecar agree **with each other**, not that either matches NRP — this table drifted once already and will again the next time NRP changes a window.

Unknown or empty model names fall back to `DEFAULT_MODEL` metadata in all three helpers (`get_client`, `model_supports_tools`, `context_budget_chars`), with one asymmetry worth knowing: `model_supports_tools` defaults an unrecognized name to `False`, routing it to the legacy path rather than raising.

---

## Non-env constants

These are literals in source. They cannot be changed without an edit and a restart. The narrative chapters explain *why* each value is what it is; this table only records what it is and where to find it.

| Constant | Value | Where defined | What it bounds |
|---|---|---|---|
| `max_iters` | `4` | `backend/helpers/agent.py :: stream_agent` (keyword default) | Tool-call iterations per agent turn, both provider paths. Exhausting it logs `agent hit max_iters=%d without stopping`. |
| `max_iters` (harness) | `8` | `backend/agent_harness.py` | The CLI harness passes a larger budget than the web path. Harness runs are not representative of production turn limits. |
| `PINNED_STUDIES_PER_CHAT_CAP` | `10` | `backend/store/cache.py` | Studies pinnable to one chat. `pin_study_to_chat` returns `False` past the cap rather than raising. |
| keyword expansion cap | `80` | `backend/services/study_service.py :: expand_keyword_variants` (`return expanded[:80]`) | Applied **after** plural/singular expansion. Since most terms yield two variants, the effective input ceiling is roughly 40 terms. |
| `_MAX_KEYWORDS_PER_PROBE` | `10` | `backend/helpers/sample_search.py` | Keywords and field filters per per-study JSONB probe; both lists are sliced to it. |
| probe `statement_timeout` | `8000` ms | `backend/helpers/sample_search.py`, from `SAMPLE_SEARCH_PROBE_TIMEOUT_MS` | Server-side kill for a single sample probe. Listed here because the value reads like a literal — it is the env default. |
| sample-search candidates | `40` / `500` | `backend/config.py`, via `SAMPLE_SEARCH_DEFAULT_CANDIDATES` / `SAMPLE_SEARCH_DEEP_CANDIDATES` | Normal vs. deep fan-out width. Also env-tunable. |
| probe `pool_size` | `16` | `backend/helpers/sample_search.py` (function default) | Ceiling on probe concurrency; actual workers are `min(len(candidate_ids), pool_size)`. Independent of `PG_POOL_MAX_CONN` — this pool is created per call. |
| probe wall-clock timeout | `max(30, len(candidate_ids) * 0.4)` s | `backend/helpers/sample_search.py` | Computed, not literal — scales with candidate count, floored at 30 seconds. |
| conversation history window | `10` | `backend/helpers/llm_helpers.py :: _normalize_messages` (`messages[-10:]`) | Most recent messages sent to the LLM. Older turns are dropped before the context budget is even applied. |
| `_STUDY_HEADER_TTL_SECONDS` | `3600` | `backend/helpers/qiita_fetch.py` | TTL on the in-process memo of study headers. Per gunicorn worker — 4 workers means up to 4 independent caches. |
| `_STUDY_DETAIL_CACHE_TTL_HOURS` | `6` | `backend/store/cache.py` | TTL on the SQLite `study_detail_cache` table. Hardcoded; there is no env override. |
| `REPORT_SAMPLE_LIMIT` | `200` | `backend/config.py` | Samples per study report. Defined among the env-driven constants but is a plain literal — the only one in that block. |
| `_MAX_STUDIES` | `5` | `backend/store/merge_crud.py` | Studies per merge workspace. `add_study_to_workspace` returns `None` past it; the route turns that into `400 "Workspace already has 5 studies (maximum)"`. |
| merge subprocess timeout | `600` s | `backend/helpers/merge_executor.py` | Wall clock for the `conda run` merge subprocess. |
| search tool result limit | `8`, clamped `1…20` | `backend/helpers/agent_tools.py` | Default and bounds for the `limit` argument the model may pass to `search_studies` / `get_study_report`. |
| LLM client timeout | `300.0` s | `backend/config.py` | Applied to both the NRP `OpenAI` client and every `anthropic.Anthropic` client. |
| `SESSION_COOKIE_NAME` | `qe_sid` | `backend/config.py` | Session cookie name. Not configurable. |
| gunicorn topology | 4 workers × 2 threads, `gthread`, 120 s timeout | `qiita_explore/start_barnacle.sh` | Process model. Multiplies every per-worker cache and pool above. |
| nginx proxy read timeout | `120` s | `qiita_explore/nginx.conf` | Must stay ≥ the gunicorn timeout or long SSE streams are cut by the proxy. `proxy_buffering off` is what makes SSE stream at all. |

---

## System prompts

Two prompts live as module-level string literals at the bottom of `backend/config.py`.

**`CHAT_SYSTEM_PROMPT`** — project-scoped chat. Consumed by `backend/helpers/llm_helpers.py`. Its work is anti-hallucination: never invent study IDs, titles, sample counts, metadata fields, or external accessions; use only what appears in the supplied project context; say "unavailable" rather than guess; answer conceptually when no studies are loaded. It also fixes the output contract (Markdown, no SQL unless asked).

**`GLOBAL_CHAT_SYSTEM_PROMPT`** — global discovery chat. Consumed by `backend/routes/global_chat_routes.py` and by `backend/agent_harness.py`. It is substantially longer because it carries the agent's operating manual: how to fill `search_studies`' typed dimension slots (`organism`, `qualifier`, `body_site`, `condition_or_intervention`, `project_or_pi`, `keywords`), how to map colloquial sequencing terms to `data_types`, the "exactly one `search_studies` call per request" rule, the top-20 result table format, and the closing refinement section.

**The distinction that matters: prompt instructions are advisory; the tool schema is mechanical.** "Issue EXACTLY ONE call per user request" is a request the model may ignore. What actually enforces it is code — `stream_agent` sets `search_already_done` after the first search and **removes `search_studies` from the tool list** on subsequent iterations, so a second call is not merely discouraged but unrepresentable. The same split applies throughout: the prompt asks for a `limit`, `backend/helpers/agent_tools.py` clamps it to `1…20`; the prompt asks the model not to over-narrow, the backend pools all slots into one ranked query regardless. When behavior must hold, look for it in the schema and the loop, not the prose. See [`05-agent.md`](05-agent.md).

Both prompts are import-time constants. Editing them requires a restart, and neither is exposed through any API.

---

## See also

- [`09-operations.md`](09-operations.md) — deployment, the start scripts, nginx, and what to check when the backend misbehaves.
- [`01-architecture.md`](01-architecture.md) — the layers these settings tune and how requests move through them.
- [`05-agent.md`](05-agent.md) — the tool-calling loop, the tool schemas, and the advisory/mechanical split described above.
