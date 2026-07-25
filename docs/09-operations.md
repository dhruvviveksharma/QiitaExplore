# 09 — Operations

*How to start the backend, what the two deployment topologies actually look like, and what each failure presents as before you know its cause.*

Prerequisites: [`01-architecture.md`](01-architecture.md) — the process model and per-worker isolation. Almost every number in this chapter is a consequence of that model.

> **Scope.** This chapter covers **running and diagnosing** a deployment that already exists. First-time setup — creating the conda environment, `pip install`, writing the Qiita config file — belongs to [`../INSTALL.md`](../INSTALL.md) and is not repeated here. If nothing has ever run on this host, start there and come back.

---

## Starting the backend

There is one supported way to start it:

```bash
bash qiita_explore/start_barnacle.sh
```

This is a repo convention stated in `CLAUDE.md`, not a preference. **Do not run `python run.py`, and do not run Flask's development server.** `run.py`'s `__main__` block exists and will start a single-process Flask server on port 5001 — it is a debugging vestige. A backend started that way has one process instead of four, no `gthread` worker class, no request timeout, and a set of cache and pool behaviors that will not match anything described in these docs. Bugs reproduced under it are not evidence about production.

### What the script does before exec'ing gunicorn

`qiita_explore/start_barnacle.sh` is short and does four things:

1. `cd`s into `backend/` — the working directory matters, because relative artifact paths resolve against it when `QIITA_BASE_DATA_DIR` is empty.
2. Activates the `qiita-web` conda environment.
3. `export`s `QIITA_CONFIG_FP` — **unconditionally**, to a hardcoded absolute path (`/home/d4sharma/qiita-web/qiita_config.cfg`). See the hazard note below.
4. `export`s `QIITA_EXPERIMENT_DB_PATH`, defaulting to `$HOME/.qiita-experiment/projects.db`, and `mkdir -p`s its parent. This one *is* overridable — it uses `${VAR:-default}`.

> **`QIITA_CONFIG_FP` cannot be overridden from the environment.** Unlike the SQLite path on the next line, it is a plain `export` with no `:-` fallback, so a value you set in your shell or in a systemd unit is silently discarded. On a host that is not the original barnacle account, this path does not exist and the vendored `qiita_core` config manager fails — there is no PostgreSQL. Editing the script is currently the only way to point it elsewhere.

Note what the script does **not** do: it does not read `qiita_explore/.env`. That file is loaded later, by `backend/config.py` at import time, inside each worker. See [Configuration](#configuration).

### Starting the pi sidecar (only if `PI_BACKEND_GLOBAL` or `PI_BACKEND_PROJECT` is set)

With both flags at their default (`false`), skip this — the backend runs exactly as it always has, and nothing tries to reach a sidecar. With either flag on, start the sidecar **before or alongside** gunicorn — Flask calls out to it synchronously mid-request, so a chat turn arriving before the sidecar is up fails with a connection error surfaced through the normal `error` SSE event, not a crash.

```bash
cd qiita_explore/pi_sidecar
npm ci   # once, or after a package-lock.json change
PI_SIDECAR_SECRET=<same value as backend's PI_SIDECAR_SECRET> bash start_sidecar.sh
```

It is a separate, independently-supervised process — `start_barnacle.sh` does not start it, and there is no process manager wiring the two together yet (this repo has none for gunicorn either; see the note on `systemctl` above). Requires Node ≥ 20 on `PATH` (developed and tested against 22.12; the package's declared `>=22.19.0` floor is a support-policy statement inherited from upstream pi, not a hard technical requirement — see `pi_sidecar/package.json`).

Health check: every route, including `/healthz`, requires the `X-Pi-Secret` header — a bare `curl http://127.0.0.1:5100/healthz` returns `401`, which just confirms the process is up and enforcing auth. Add the header to confirm it's actually healthy: `curl -H "X-Pi-Secret: $PI_SIDECAR_SECRET" http://127.0.0.1:5100/healthz` → `{"ok":true,"cachedSessions":N}`.

### The gunicorn flags, and why each one is there

```
gunicorn -w 4 --threads 2 -b 0.0.0.0:5001 \
  --timeout 300 --graceful-timeout 30 \
  --worker-class gthread --log-level info run:app
```

| Flag | Why it is set to this |
|---|---|
| `-w 4` | Four forked worker processes. Every in-process cache and connection pool is multiplied by this number — see [Capacity](#capacity). |
| `--threads 2` | Two request threads per worker, so **8 concurrent requests** total. Chat requests hold a thread for the whole stream, which is what makes this number matter. |
| `--worker-class gthread` | Required for `--threads` to mean anything. It is also the reason the vendored `TRN` transaction singleton is a hazard: two threads in one worker share it. |
| `-b 0.0.0.0:5001` | Binds all interfaces. In production nginx is the only intended client; nothing in the app restricts who may connect directly to 5001. |
| `--timeout 300` | Gunicorn kills a worker whose request has not completed in 300 s. Raised from 120s when the pi backend landed (see below). |
| `--graceful-timeout 30` | On restart, workers get 30 s to finish in-flight work before `SIGKILL`. |
| `--log-level info` | Gunicorn's *error* log level. It does not enable an access log — see [Logging](#logging-and-observability). |

**`--timeout 300` versus SSE.** This is a whole-request timeout, and an SSE chat stream *is* one request. A turn that takes longer than 300 seconds has its worker killed mid-stream. The browser sees a truncated stream, not an error message, because the SSE `done` event never arrives. nginx's `proxy_read_timeout 300s` is set to match; the two numbers are meant to move together, and raising one without the other converts a clean cut into a confusing one.

**Why 300, not 120.** The original 120s budget was already tight for a single slow NRP completion (the OpenAI/Anthropic clients themselves use a 300s timeout — see [`appendix-d-configuration.md`](appendix-d-configuration.md)). Behind `PI_BACKEND_GLOBAL`/`PI_BACKEND_PROJECT`, a turn can additionally include pi's own compaction pass and several sequential tool round-trips to the sidecar (each itself a synchronous HTTP call from Flask to `PI_SIDECAR_URL`) before the model produces its final answer — comfortably capable of exceeding 120s on a loaded NRP endpoint even without anything going wrong. **The sidecar has no request-level timeout of its own** (`pi_sidecar/server.mjs` streams for as long as the pi turn runs); gunicorn's `--timeout` remains the only backstop, which is why it moved rather than being left for the sidecar to enforce.

Note that gunicorn's `--timeout` is a *worker liveness* check, not a wall-clock request budget in the usual sense, but for a synchronous streaming response the practical effect is the same: exceed it and the worker is recycled.

**`--graceful-timeout 30` versus background work.** This is the more damaging of the two. On restart a worker has 30 seconds to drain, and two kinds of work do not survive:

- **In-flight streams** longer than 30 s are cut. The client sees a partial answer.
- **Background merge jobs are orphaned.** A merge runs inside the accepting worker's `_bg_executor`, not in a separate process or queue. When that worker exits, the job's `conda run` subprocess and the thread waiting on it are gone, but the `merge_jobs` row was already set to `running` and **nothing will ever move it off that state**. There is no reaper, no heartbeat, and no startup scan for stale jobs. See [Failure modes](#failure-modes).

A restart is therefore never free while a merge is running. Check for `running` jobs before recycling if you can.

### Restarting, and what a restart costs

There is no zero-downtime story here. `start_barnacle.sh` ends in `exec gunicorn`, so the shell process *is* gunicorn and the ordinary stop-then-start is what the environment script suggests: `pkill -f gunicorn` followed by re-running the script, or `systemctl restart` where the backend runs as a unit.

Four things are lost or degraded on every restart, in rough order of how much they matter:

- **Running merge jobs are orphaned** — permanently stuck in `running`, as above. This is the only *irrecoverable* loss.
- **In-flight streams past the 30-second graceful window are cut**, and the client sees a truncated answer rather than an error.
- **Every in-process cache goes cold.** The per-worker study-header memo (`_STUDY_HEADER_TTL_SECONDS = 3600`) is a plain dict and does not survive. All four workers start cold and repopulate independently, so the first minutes after a restart put noticeably more load on PostgreSQL than steady state.
- **Connection pools are rebuilt lazily.** `get_pool` constructs on first use rather than at import — deliberately, because gunicorn forks after import and an inherited pool would share sockets across processes. The first request into each worker pays the connect cost.

What *does* survive is everything in SQLite: sessions, projects, chats, and the `study_detail_cache` and `biom_sample_cache` layers. Users stay logged in across a restart; only unverified in-flight work is lost.

Schema migration needs no separate step and cannot be run separately — importing anything from `store` bootstraps and migrates the SQLite database as an import side effect, once per worker at startup. That also means a migration failure presents as a worker that failed to boot, not as a failed migration command.

---

## The two topologies

Both are current. Confusing them wastes a great deal of time. [`01-architecture.md`](01-architecture.md) has the diagrams; this section covers only what an operator needs to act on.

### Production — nginx :8080 → gunicorn :5001

nginx terminates on **8080**, serves the static frontend from `location /`, and proxies `location /api/` to gunicorn on **127.0.0.1:5001** through an upstream block with `keepalive 32`. Frontend and API are same-origin, which is why an empty `QIITA_EXPLORE_ALLOWED_ORIGINS` is the correct production value.

The port map is small enough to hold in your head:

| Port | Listener | Reached by |
|---|---|---|
| `8080` | nginx | the browser — both the frontend and `/api/` |
| `5001` | gunicorn, bound `0.0.0.0` | nginx's upstream block. Nothing in the app restricts direct clients. |
| — | classic Qiita PostgreSQL | each worker's pool, credentials from `QIITA_CONFIG_FP` |
| — | Qiita-MIINT control plane | `whoami` only, at `QIITA_CONTROL_PLANE_URL` |

Note that `QIITA_CONTROL_PLANE_URL`'s default, `http://127.0.0.1:8080`, **collides with the port nginx listens on**. Any real deployment has to set it; leaving the default in place points the backend's identity calls at its own web server.

One thing must be edited before `nginx.conf` works anywhere: `location /` has `root /path/to/qiita_explore/frontend;` — a literal placeholder. Deployed unchanged, every API call succeeds and every page load 404s.

### Development — SSH tunnels and port 5002

The dev topology runs the backend on **5002** on barnacle as a systemd service, the control plane on the developer's Mac at `127.0.0.1:8080`, and the frontend from a static server on the Mac at `:5503`. `Qiita/start_qiita_stack.sh` brings the whole thing up in one command. Two tunnels connect it:

| Direction | Kind | Mapping | Carries |
|---|---|---|---|
| Browser → backend | **forward** | `Mac:5002 → barnacle:5002` | All API traffic from the developer's browser |
| Backend → control plane | **reverse** | `barnacle:18080 → Mac:8080` | `whoami` — every PAT validation |

`frontend/index.html` ships with `<meta name="api-base" content="http://127.0.0.1:5002/api">` and a TODO to revert it to 5001 before merging to master. That is the port convention in `CLAUDE.md`, not a bug.

> **The reverse tunnel is the one that breaks, and its signature is specific.** If `barnacle:18080 → Mac:8080` is down, the backend is perfectly healthy but cannot reach the control plane. Every `whoami` fails as a *connect error*, which `backend/helpers/qiita_client.py :: whoami` classifies as `transient_error=True`. The middleware then returns **503 "Qiita is temporarily unreachable, try again shortly"** and — deliberately — leaves every session row intact. Users are not logged out; the app sits in its `unavailable` state showing "Reconnecting to Qiita…". Nothing recovers on its own until the tunnel is back. Check it with `pgrep -f 'ssh.*-R 18080:localhost:8080'`, which is exactly what `start_qiita_stack.sh` does in its status block.

The forward tunnel fails far more visibly: the browser cannot reach the API at all, so the failure announces itself on the first request rather than presenting as a degraded auth state.

`start_qiita_stack.sh` is idempotent — it probes each component and skips whatever is already up — so re-running it is the standard first response to any dev-topology oddity. It also chooses which control-plane build to launch: `PATCHED=1` (the default) applies the AuthRocket logout-first fix at runtime via `Qiita/run_patched_control_plane.py`, leaving the Qiita repo untouched at master. `PATCHED=0` runs the plain build and reintroduces the account-hijack behavior described in [`02-authentication.md`](02-authentication.md); it exists for debugging only.

---

## nginx settings that are required, not incidental

`qiita_explore/nginx.conf` is under forty lines, and nearly every directive in the `/api/` block exists because of SSE. Treat this as a list of things that must not be "cleaned up".

| Directive | Why it exists | Symptom if wrong or missing |
|---|---|---|
| `proxy_buffering off` | nginx would otherwise accumulate the response body and forward it once complete. | The chat answer appears **all at once** at the end, after a long pause. The single most common "streaming is broken" cause. |
| `proxy_request_buffering off` | Same, for the request direction. | Uploads and large request bodies buffer before the app sees them; delayed request start. |
| `proxy_read_timeout 120s` | Must be ≥ gunicorn's `--timeout 120` so the proxy is not the thing that cuts a long turn. | A long agent turn is severed by nginx with a 504 before gunicorn has any say. |
| `proxy_connect_timeout 10s` | Bounds how long nginx waits to reach a gunicorn that is restarting. | Requests hang for the default (60 s) during a restart instead of failing fast. |
| `proxy_http_version 1.1` | HTTP/1.0 has no chunked transfer encoding and no keepalive. | Streaming does not work at all; the `keepalive 32` upstream block is ignored. |
| `proxy_set_header Connection ""` | Clears the inherited `close` header so upstream keepalive is used. | Connection churn against gunicorn — a new TCP connection per request. |
| `proxy_pass_header X-Accel-Buffering` | The app sets `X-Accel-Buffering: no` on every SSE response (`backend/helpers/request_utils.py`); without this nginx strips it. | Loses the app's own anti-buffering signal — a second line of defense, not the primary one. |

> **Unverified.** The `server` block sets `gzip on` with `gzip_types application/json text/event-stream text/plain` and `gzip_min_length 1024`. gzip is a **response-body filter that runs independently of `proxy_buffering`**, so `proxy_buffering off` does not exempt a stream from it, and `gzip_min_length` keys off `Content-Length` — which a chunked SSE response does not send. Whether this actually delays frames in this deployment **has not been measured**, and no stutter has been attributed to it. It is recorded rather than fixed for that reason. If streaming is observed to stutter while `proxy_buffering off` is confirmed present, **removing `text/event-stream` from `gzip_types` is the first thing to try**, before investigating the app.

---

## Configuration

The full table of every variable — defaults, consumers, whether a restart is needed — is [`appendix-d-configuration.md`](appendix-d-configuration.md). This section covers only what an operator has to get right, and the one gotcha that costs the most time.

### Configuration comes from a `.env` **file**, not from the shell

`backend/config.py` calls `load_dotenv()` at module import. python-dotenv walks up from `config.py`'s directory and finds **`qiita_explore/.env`**. The backend runs under gunicorn as a service and inherits essentially nothing from the shell you were sitting in.

The consequences, stated plainly:

- `export FOO=bar` in your terminal does nothing to the running backend. Neither does `~/.bashrc`.
- To change a setting: **edit `qiita_explore/.env`, then restart gunicorn.** There is no reload endpoint and no file watcher, because `config.py` binds every value to a module-level name at import.
- The only variables that legitimately live in the shell are the two `start_barnacle.sh` exports itself.
- `Qiita/barnacle_backend_env.sh` exists precisely because of this. Run on barnacle, it idempotently pins `QIITA_CONTROL_PLANE_URL` and `QIITA_PUBLIC_LOGIN_URL` into the `.env` file (backing it up first), deletes `QIITA_LOGINROCKET_URL`, and prints the restart command. It edits the file, not the environment.

The one exception to "restart required" is `ANTHROPIC_API_KEY`, which is re-read per request from the SQLite `meta` table — see appendix D.

### The operational subset — what must be set for a healthy boot

| Variable | If missing |
|---|---|
| `QIITA_EXPLORE_PAT_ENCRYPTION_KEY` | Process boots **clean**, then every login and every PAT re-verification fails. Top diagnostic. |
| `QIITA_CONFIG_FP` | Vendored config manager raises `KeyError` — no PostgreSQL credentials, no study data at all. |
| `OPENAI_API_KEY` or `API_KEY` | The OpenAI client is built with `api_key=None`; every NRP model call fails at request time. |
| `QIITA_CONTROL_PLANE_URL` | Defaults to `http://127.0.0.1:8080` — loopback, and it collides with nginx's own port. |
| `QIITA_BASE_DATA_DIR` | Artifact paths resolve relative to gunicorn's working directory. Fails later as "file not found", far from the cause. |

> **The encryption-key failure is deferred, and that is what makes it hard.** `config.py` reads `QIITA_EXPLORE_PAT_ENCRYPTION_KEY` with `os.getenv` and never validates it. `backend/helpers/pat_crypto.py :: _get_fernet` raises `PatCryptoError` only when first *called* — which is the first PAT encrypt or decrypt, i.e. the first login. So the process starts, logs nothing unusual, serves `/api/systems` happily, and cannot authenticate anyone. **If every login fails while the process looks healthy, check this variable before anything else.** The `.env` checked into the repo does not set it.

`QIITA_LOGINROCKET_URL` must stay **unset**. It configures a login flow that appears correct and misbehaves; `barnacle_backend_env.sh` actively deletes it. The reasoning is in [`02-authentication.md`](02-authentication.md).

### Two requirements files, and which one is authoritative

`qiita_explore/requirements.txt` is what [`../INSTALL.md`](../INSTALL.md) installs. It uses **ranges** (`flask>=2.2,<2.3`, `openai>=1.40,<3.0`, `psycopg2-binary` unpinned).

`qiita_explore/requirements.prod.txt` covers the same dependency set with **exact pins** (`Flask==2.2.5`, `pandas==1.5.3`, `numpy==1.26.4`), and its header still describes an "ezredbiom container" — the tree's former name.

Nothing in the start scripts or CI selects between them, so which one a host has installed is a property of how that host was built, not of the repo. Two consequences worth knowing before debugging a version-shaped problem: a fresh environment built from `requirements.txt` today will not necessarily match a host built from it six months ago, and the two files are kept in sync by hand — a dependency added to one is not automatically in the other. When a bug reproduces on one host and not another, `pip freeze` on both is the fastest discriminator.

Both files install `qiita-files` directly from a GitHub archive URL at `master`, so a rebuild also picks up whatever that branch holds at rebuild time.

---

## Logging and observability

### What is emitted, and where it lands

`backend/run.py` calls `logging.basicConfig(level=logging.INFO)` with the format `%(asctime)s [%(levelname)s] %(name)s: %(message)s`, then forces the root logger to `INFO`. Fourteen backend modules take a `logging.getLogger(__name__)`, so the module name in each line tells you the subsystem.

`basicConfig` installs a `StreamHandler` on stderr, and gunicorn's own error log also goes to stderr. Both therefore land in the same place — journald if the backend runs as a systemd service, or wherever stderr was redirected if it was launched by hand.

There is **no access log.** `--log-level info` sets the *error* log level; gunicorn's access log is off unless `--access-logfile` is passed, and `start_barnacle.sh` does not pass it. Per-request lines — status codes, paths, durations — do not exist. Adding `--access-logfile -` is the change to make if you need them.

### Useful things to grep for

Log messages carry stable bracketed prefixes, which is the closest thing this system has to structured logging:

| Grep for | Tells you |
|---|---|
| `[agent_start]` | An agent turn began: model, resolved model, deep-search flag, message count. |
| `[timing] tool=` | Per-tool-call elapsed seconds and result size. The main latency signal. |
| `[search_studies]` | Expanded keywords, effective data types, text-hit count, sample-hit count, final merged count. Five lines per search — the whole search pipeline. |
| `[sql] rows_returned=` | Row count from the study-search query. |
| `[merge:<job_id>]` | Merge subprocess stdout (INFO) and stderr (WARNING), plus `job failed` with a traceback. |
| `whoami transient failure` | Control plane unreachable — the reverse-tunnel signature. |
| `hit max_iters` | An agent turn exhausted its four tool-call iterations without concluding. |
| `probe study=... failed` | A single sample-search probe failed or timed out. Expected occasionally; a flood is not. |

### What is not observable

This should be read as a list, not a footnote, because it bounds what any incident response can accomplish:

- **No metrics.** No counters, no histograms, no Prometheus endpoint. Request rates, error rates, and latency percentiles are not recorded anywhere. The `[timing]` log lines are the only latency data that exists, they cover tool calls only, and reading them means parsing text.
- **No tracing.** No request IDs. Two concurrent chats interleave their log lines with nothing to separate them beyond the study or chat IDs that happen to appear in some messages.
- **No health endpoint.** There is no `/healthz`, no readiness probe, and no liveness check the app itself answers.
- **`/api/systems` is not a health check.** `backend/routes/study_routes.py :: api_systems` fans out across a thread pool and sends a real one-token `"Hi"` completion to **every model in `ALLOWED_MODELS`** with a 15-second per-model timeout, reporting `ok`/`down` and a latency figure for each. It probes the *LLM providers*, not this application — it will report every model `ok` on a backend whose PostgreSQL is unreachable and whose encryption key is missing. It also requires an authenticated session (it is not in `PUBLIC_ENDPOINTS`), so it cannot serve as an unauthenticated liveness probe, and each call costs one upstream request per model.
- **Background work is unobserved.** Project enrichment, preload, and merges are submitted to `_bg_executor` with their futures dropped. Exceptions surface only as log lines.
- **Queue depth is invisible.** When all 8 request slots are busy, the ninth request waits in the kernel's listen backlog. Nothing counts or reports that wait, so saturation presents to users as unexplained slowness with no corresponding signal on the server.
- **The data layer degrades silently by design.** `qiita_fetch :: _qiita_fetch` swallows exceptions and returns a caller-supplied default; `artifact_graph :: fetch_artifact_graph` logs and returns `[]`. A persistent upstream problem presents in the UI as *missing data*, not as an error. See [`03-data-access-and-caching.md`](03-data-access-and-caching.md).
- **`AGENT_DEBUG` does not affect the backend.** It is read only by `backend/agent_harness.py`, the offline CLI driver, where it raises that process's log level to `DEBUG`. Setting it in the backend's `.env` changes nothing. To get agent-loop detail out of a running gunicorn you must raise the root level in `run.py` and restart.

### Verifying a deployment by hand

Because there is no health endpoint, "is it up and correct?" is a manual sequence. Each step exercises a different dependency, and they are ordered so the first failure names the layer:

1. **Process and port.** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5001/api/auth/login-url` — expect `200`. This endpoint is public, so it needs no session, and it proves gunicorn is up and `config.py` imported.
2. **Through the proxy.** Same request against `:8080`. A difference between the two isolates nginx.
3. **Control plane reachability.** From the backend host, `curl -s -o /dev/null -w '%{http_code}\n' <QIITA_CONTROL_PLANE_URL>/api/v1/auth/whoami`. Anything that is not a connection failure is fine here — a 401 is a healthy answer to an unauthenticated probe.
4. **PostgreSQL.** Hit any study-listing endpoint with a valid session and confirm rows come back. There is no lighter probe; a failure here degrades to *empty results* rather than an error, so an empty grid is the symptom to watch for.
5. **Authentication end to end.** Complete one real login. This is the only step that exercises `pat_crypto`, and given the deferred-failure behavior above it is the only step that can prove the encryption key is right.
6. **Streaming.** Send one chat message and watch it render token by token rather than in a single block. This is the only check that covers the nginx SSE directives.

The dev topology's equivalent is the status block `Qiita/start_qiita_stack.sh` prints on exit: control plane, backend through the forward tunnel, and reverse-tunnel presence.

---

## Failure modes

The centerpiece. Symptoms are listed as they present to a user or an operator, not as they are caused.

| Symptom | Likely cause | Where to look | Fix |
|---|---|---|---|
| **Chat answer arrives all at once** after a long pause, instead of streaming | nginx buffering the response | `nginx.conf` `/api/` block | Confirm `proxy_buffering off` and `proxy_request_buffering off` are present and that the running config is the one on disk (`nginx -T`). If both are set, next suspect is the gzip/SSE item above — remove `text/event-stream` from `gzip_types` and retest. |
| **Every login fails, process looks healthy**; `/api/systems` reports models `ok` | `QIITA_EXPLORE_PAT_ENCRYPTION_KEY` unset or not a valid Fernet key | Logs for `PatCryptoError` / "PAT encryption failed"; `backend/helpers/pat_crypto.py :: _get_fernet` | Set the key in `qiita_explore/.env` and restart. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Existing stored PATs encrypted under a *different* key cannot be recovered — those sessions must reconnect. |
| **"Qiita is temporarily unreachable, try again shortly"** (503), users stay logged in | Control plane unreachable. In dev: the **reverse** SSH tunnel is down | `pgrep -f 'ssh.*-R 18080:localhost:8080'`; logs for `whoami transient failure` | Re-run `bash Qiita/start_qiita_stack.sh` (idempotent — it skips what is already up). In production, verify `QIITA_CONTROL_PLANE_URL` resolves and responds from the backend host. |
| **All users appear logged out at once** | Not transient — a genuine revocation path. Either the SQLite store was replaced/moved, or the encryption key changed (decrypt failure revokes), or `AUTH_SESSION_*` TTLs were shortened | `QIITA_EXPERIMENT_DB_PATH`; `auth_sessions.revoked_at`; logs for "failed to decrypt stored PAT during reverify" | Restore the correct DB path or key. Note the design intent: a *transient* upstream failure never logs anyone out, so mass logout means something local changed. |
| **Search times out or returns very few results** | Per-probe `statement_timeout` (8 s) killing sample probes, or the candidate set being too narrow | Logs for `[search_studies] text_hits=` / `sample_hits=` and `probe study=... failed` | Compare `text_hits` against `sample_hits` to see which half is starving. Raise `SAMPLE_SEARCH_PROBE_TIMEOUT_MS` or `SAMPLE_SEARCH_DEFAULT_CANDIDATES` — both cost PostgreSQL load. Sample search returns partial results rather than failing, so "too few" is the expected shape of this failure. |
| **Stale study data after a Qiita-side change** | Layered caching: a per-worker 1-hour header memo plus a 6-hour SQLite `study_detail_cache` | `study_detail_cache` rows; [`03-data-access-and-caching.md`](03-data-access-and-caching.md) | Restart gunicorn to clear the in-process memo. The SQLite layer needs its rows deleted — its 6-hour TTL is hardcoded, not tunable, and a partial refresh resets the clock for the whole row. Expect inconsistency between workers until both layers turn over. |
| **Merge job stuck in `running` forever** | The accepting worker restarted or was killed; the job lived only in that worker's `_bg_executor` | `merge_jobs` rows with `status='running'` and an old `updated_at`; absence of `[merge:<job_id>]` lines after a point | No automatic recovery exists. Update the row to `failed` manually and resubmit. Nothing reaps these — see [Routine maintenance](#routine-maintenance). |
| **Merge job fails immediately** on barnacle | The merge executor is dev-only. It shells out to `conda run -n <MERGE_CONDA_ENV>` on the **local** host and reads BIOM files from local paths (TKT-015) | `backend/helpers/merge_executor.py` module docstring; logs for `[merge:<job_id>]` stderr lines | Not a configuration problem — the code path assumes a local conda env and local artifact files. The module's own TODO is to replace it with an SFTP+SSH pipeline. Verify `MERGE_CONDA_ENV` exists locally as a first check, then treat it as known-unfinished. |
| **Boot fails on a fresh checkout** | Usually `QIITA_CONFIG_FP` — hardcoded to one machine's absolute path, and not overridable from the environment | Traceback naming `qiita_core/configuration_manager.py`, or a `KeyError` on the variable | Edit `start_barnacle.sh` to point at this host's config file. Also confirm the `qiita-web` conda env exists — see [`../INSTALL.md`](../INSTALL.md). |
| **PostgreSQL connection exhaustion** (`FATAL: sorry, too many clients`) | Per-worker pools multiply; sample search adds a fresh pool per call | `PG_POOL_MAX_CONN`; server-side `pg_stat_activity` | Do the arithmetic in [Capacity](#capacity) before changing anything. Lowering `PG_POOL_MAX_CONN` or the worker count both help; lowering `SAMPLE_SEARCH_*` candidate counts reduces the spiky component. |
| **`database is locked` from SQLite** | Many writers on one file, and no `busy_timeout` beyond Python's 5-second default | `backend/store/db.py :: _conn` | See the hazard note in [Capacity](#capacity). Reducing worker count reduces contention; the real fix is a longer busy timeout. |
| **Frontend 404s while the API works** | `nginx.conf`'s `location /` still has the literal `root /path/to/qiita_explore/frontend;` | `nginx.conf` | Set `root` to this deployment's absolute frontend path. |
| **Login succeeds, then the next request is 401** — the session cookie never comes back | Cookie rejected by the browser. `qe_sid` is set `HttpOnly`, `SameSite=Lax`, `Secure=QIITA_EXPLORE_COOKIE_SECURE` (default `true`), `path=/` | `backend/routes/auth_routes.py :: _cookie_kwargs`; the browser's cookie inspector | A `Secure` cookie over plain HTTP is dropped on any origin the browser does not treat as trustworthy. `localhost` and `127.0.0.1` are exempt, which is why the dev topology works — a plain-HTTP deployment on a real hostname is not. Serve over TLS, or set the variable `false` for that deployment only. |
| **Cross-origin requests fail, or a token post is accepted from an unexpected page** | `QIITA_EXPLORE_ALLOWED_ORIGINS` empty in a deployment that is *not* same-origin | `backend/routes/auth_routes.py :: _origin_allowed` | Empty is correct behind nginx, where frontend and API share an origin. In a split deployment it means both no CORS *and* an Origin check on `/auth/connect` that passes unconditionally. Set the exact origins. |
| **Exception internals appear in an API response body** | `QIITA_EXPLORE_DEBUG_ERRORS` left truthy after debugging | `qiita_explore/.env` | Unset it and restart. It leaks exception type and message to unauthenticated callers of `/auth/connect`. |

---

## Capacity

Do this arithmetic before raising the worker count. Every figure below is a direct consequence of gunicorn forking four processes that share nothing in Python memory ([`01-architecture.md`](01-architecture.md)).

Let **W** = workers (`-w`, currently 4) and **T** = threads per worker (`--threads`, currently 2).

### Concurrent requests

```
concurrent requests = W × T = 4 × 2 = 8
```

This is a hard ceiling on requests *in flight*, and chat is the reason it binds: an SSE stream occupies its thread for the entire turn, not for a few milliseconds. Eight users mid-conversation saturate the deployment; the ninth request queues.

### PostgreSQL connections

Two components, one steady and one spiky.

```
steady   = W × PG_POOL_MAX_CONN            = 4 × 8  = 32
spiky    = (concurrent sample searches) × probe_pool_size,  probe_pool_size ≤ 16
worst    = W × (PG_POOL_MAX_CONN + T × 16) = 4 × (8 + 32) = 160
```

The steady term is the shared `ThreadedConnectionPool(2, 8)` that each worker builds lazily on first use. **`PG_POOL_MAX_CONN=8` is a per-worker setting** — the deployment's real ceiling is 32, not 8.

The spiky term is `sample_search`, which opens a **fresh pool per call** (it needs its own `statement_timeout`, which cannot be attached to a borrowed shared connection) sized `min(len(candidate_ids), 16)`. Every concurrent sample search adds up to 16 more connections for its duration. The worst case above assumes every request thread is running one simultaneously — unlikely, but it is the number PostgreSQL's `max_connections` has to survive.

Deep search (`SAMPLE_SEARCH_DEEP_CANDIDATES=500` versus `40`) does not raise the connection ceiling — the pool is still capped at 16 — but it lengthens how long those connections are held, which makes concurrent spikes overlap more.

### Threads

```
per worker (worst)  = T request threads + T × 16 probe threads + 4 _bg_executor = 2 + 32 + 4 = 38
deployment          = W × 38 = 152
```

`_bg_executor` is `ThreadPoolExecutor(max_workers=4)` **per worker**, so 16 background slots total. Work submitted by a request is visible only to the worker that accepted it — which is why a merge cannot be picked up by another worker after a restart.

### SQLite

One file, WAL mode, `synchronous=NORMAL`. WAL permits concurrent readers alongside a single writer, which is what makes four processes on one file viable at all. But the writer count is:

```
potential writers = W × (T + 4) = 4 × 6 = 24
```

> **Hazard not otherwise documented.** `backend/store/db.py :: _conn` calls `sqlite3.connect(DB_PATH)` with **no `timeout=` argument and no `PRAGMA busy_timeout`**. Python's default is 5 seconds. Under contention, a writer that cannot acquire the lock within 5 seconds raises `sqlite3.OperationalError: database is locked` — surfacing as a 500 on whatever request happened to be writing. Nothing in the current worker count makes this likely, but it scales badly: doubling `-w` doubles the writer population against an unchanged lock. Setting an explicit `busy_timeout` is a small, low-risk change worth making before any capacity increase.

### Worked example — doubling to 8 workers

Substituting **W = 8** into the formulas above, before touching anything else:

| Quantity | At W=4 | At W=8 |
|---|---|---|
| Concurrent requests | 8 | 16 |
| Steady PG connections | 32 | 64 |
| Worst-case PG connections | 160 | 320 |
| `_bg_executor` slots | 16 | 32 |
| Potential SQLite writers | 24 | 48 |
| Independent header-memo copies | 4 | 8 |

The steady figure is the one to check against PostgreSQL's `max_connections`, which is shared with every other client of that database — including classic Qiita itself. The worst-case figure is what a burst of concurrent sample searches can produce, and it is the number that causes `FATAL: sorry, too many clients` when it is not budgeted for. If 64 steady connections is not available, lower `PG_POOL_MAX_CONN` in the same change: at W=8, `PG_POOL_MAX_CONN=4` restores the original steady ceiling of 32.

Doubling the memo copies is the quieter cost. A study header cached on one worker is cold on the other seven, so the effective miss rate on that layer rises with W, and PostgreSQL sees more header reads even at unchanged user load.

### The rule of thumb

Raising `-w` multiplies, simultaneously: PostgreSQL connections, background thread slots, SQLite writers, and the number of independent copies of the study-header memo (which raises the cache miss rate proportionally — a header cached on one worker is cold on the others). It is not a free knob. If the bottleneck is concurrent *streams*, raising `--threads` is cheaper: it adds request capacity without adding another PG pool or another cache copy, at the cost of more threads contending inside one GIL.

---

## Routine maintenance

Nothing on this list is automated. All four items grow without bound.

**Expired session rows accumulate forever.** `backend/store/auth_store.py :: purge_expired_sessions` exists, is correct, and hard-deletes rows past absolute expiry while deliberately keeping revoked rows for audit. **It is never called** — no route, no scheduler, no startup hook. This is a storage-growth issue and not a security one: `get_session_by_token` rejects expired sessions regardless of whether their rows are still present. Until it is wired up, calling it periodically out-of-band is the workaround.

**Merge result tarballs are never cleaned up.** Verified in `backend/helpers/merge_executor.py :: run_merge_job`: the temporary job directory is `shutil.rmtree`'d in a `finally` block, but the final `{job_id}.tar.gz` is moved into `MERGE_RESULTS_DIR` and left there permanently. No route deletes it, no TTL applies, and deleting a merge job or its workspace does not remove the file. These are full merged BIOM bundles, so the directory grows in proportion to total merges ever run, not to merges currently useful. It needs periodic pruning against the `merge_jobs` table.

**Log rotation is external.** The app writes to stderr and configures no file handler or rotation. Under systemd, journald's own retention policy applies and there is nothing to do. If the backend is launched by hand with stderr redirected to a file, that file grows without limit — `logrotate` or an equivalent is the operator's responsibility.

**SQLite growth.** Beyond sessions, three tables grow monotonically with use: `study_detail_cache` (rows are refreshed but never evicted — the TTL governs whether a row is *trusted*, not whether it is *kept*), `biom_sample_cache` (permanent by design, since Qiita artifacts are immutable and their sample IDs cannot change), and chat message history (`project_chat_messages`, `global_chat_messages`, including a `ui_payload` blob per agentic turn). WAL checkpointing keeps the `-wal` file bounded in normal operation; the main file only grows unless `VACUUM` is run.

**Orphaned merge jobs.** Related to the above and worth a periodic query: rows in `merge_jobs` with `status='running'` and an `updated_at` older than the merge subprocess timeout (600 s) plus a margin are almost certainly orphaned by a worker restart and will never change state on their own. A job's states are `pending` (written by `create_merge_job` at submission), then `running`, then `done` or `failed` — all four written through the single `_on_status` callback in `backend/routes/merge_routes.py :: submit_merge_job`. Nothing else transitions them.

**`.env` backups accumulate, and they hold secrets.** `Qiita/barnacle_backend_env.sh` runs `cp "${ENV_FILE}" "${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"` on **every** invocation, and never prunes. Each backup is a full copy of the backend's `.env`, which on a correctly configured host contains the LLM API key and the Fernet PAT-encryption key in plaintext. The script is safe to re-run — that is the point of it — but re-running it leaves a growing set of timestamped secret files beside the live one. Prune them, and keep the directory's permissions in mind when you do.
>
> Tracked as **TKT-045**. Note that `helpers/pat_crypto.py` keeps the Fernet key out of SQLite specifically so a leaked database file does not also leak PATs — plaintext copies beside the database partly defeat that, so this is worth treating as a live exposure rather than housekeeping.

**pi sidecar session files (only relevant if `PI_BACKEND_GLOBAL`/`PI_BACKEND_PROJECT` is or was ever on).** Each chat gets one JSONL session file under `pi_sidecar/.state/sessions/`. Deleting a chat calls `POST /session/delete` on the sidecar as a best-effort cleanup (`backend/helpers/pi_client.py :: delete_session`), which removes it — but that call is skipped entirely if the deleting request's flag is off, and it's fire-and-log-only if the sidecar is unreachable at delete time. There is no reaper for orphaned session files independent of chat deletion, mirroring the pattern above: periodic pruning against SQLite's chat tables (a session file with no matching `chat_id` in either `project_chats` or `global_chats` is safe to remove) is the workaround until one exists.

**None of this is scheduled.** There is no cron entry, no APScheduler, and no startup hook in the repo that performs any of the above. If these tasks are running on a host, they were added out-of-band and are not described by anything checked in.

---

*See also: [`01-architecture.md`](01-architecture.md) for the process model these numbers derive from · [`appendix-d-configuration.md`](appendix-d-configuration.md) for every variable and its default · [`02-authentication.md`](02-authentication.md) for session and PAT failure semantics · [`../INSTALL.md`](../INSTALL.md) for first-time setup.*
