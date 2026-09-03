# 10 — Testing

*What the suite actually guards, what it cannot run without a live backend, and the one test this repository most needs and does not have.*

Prerequisites: [`06-streaming-and-chat.md`](06-streaming-and-chat.md) — the dual-authoring hazard that motivates the headline recommendation below.

---

## The three tiers

The suite splits into three tiers with sharply different external requirements. The distinction matters more than usual here, because **only one tier runs on a laptop with nothing else started**.

| Tier | Location | What it covers | Requires | How to run |
|---|---|---|---|---|
| **Unit** | `backend/tests/*.py` — 8 test files, 95 test functions | SQLite CRUD, schema and cascade integrity, pin store, auth (crypto, sessions, routes, isolation), merge metadata assembly, one agent generator | Nothing external. No PostgreSQL, no LLM, no conda | `bash run_tests.sh --unit`, or `python3 -m pytest tests/ -m "not e2e"` |
| **e2e** | `backend/tests/e2e/` — 6 test files, 36 test functions, plus `conftest.py` and `parity_helpers.py` | Search parity, visibility enforcement, pin SSE flow, deep search, agent tool leakage | A **running barnacle backend**, which in turn needs live Qiita PostgreSQL. The `e2e_llm` subset also needs a reachable LLM endpoint and an API key for the judge | `bash run_tests.sh` (deterministic), `--llm` (judge subset), `--all` |
| **Benchmarks** | `backend/tests/benchmarks/` — 4 standalone scripts | Search latency, cache speedup, concurrency, cache hit rate | Same as e2e: live backend and PostgreSQL | `python search_latency.py` etc., by hand. Not collected by pytest |

Nothing in any tier requires a conda environment, despite the merge executor shelling out to `conda run` at runtime — see the merge gap below.

Dependencies are thin. `backend/tests/requirements.txt` lists exactly two packages:

```
pytest>=7.0
requests>=2.28
```

`requests` is only used by the e2e and benchmark tiers. The unit tier additionally leans on packages that are already backend runtime dependencies — `httpx` and Flask itself — so a working backend virtualenv plus these two is sufficient.

`backend/pytest.ini` declares only two markers, and that is the whole selection mechanism:

- `e2e` — needs a running backend on `:5001` (the ini comment says `:5002`, a stale dev-port reference) and live Qiita Postgres.
- `e2e_llm` — the subset that goes through the LLM; explicitly documented as flaky and intended for nightly runs.

Coverage configuration lives at the **repository root**, not next to the tests: `/.coveragerc` omits `qiita_db/support_files/**/*` and sets `relative_files = True`. No coverage threshold is enforced and no CI configuration invokes it.

---

## Unit tests

One line each, in the order a newcomer should read them.

| File | What it asserts |
|---|---|
| `backend/tests/test_crud.py` | Project create / list / get / update / delete, ordering by recency, per-`user_id` isolation, and the `studies_count` rollup |
| `backend/tests/test_studies.py` | Adding and removing studies from a project, no duplicate rows on repeated add, survival across re-fetch |
| `backend/tests/test_chats.py` | Project-chat and global-chat CRUD, message append order and roles, global chats isolated per user |
| `backend/tests/test_api.py` | Schema integrity — expected tables and indexes exist, `PRAGMA foreign_keys` is on, project delete cascades to studies and chats, timestamps are UTC with a `Z` suffix |
| `backend/tests/test_pin_command.py` | Store-level pinning: idempotent re-pin, the per-chat cap read from `PINNED_STUDIES_PER_CHAT_CAP`, unpin, and project/global scope isolation in both directions |
| `backend/tests/test_agent_tool_call.py` | `helpers/agent.py :: _execute_tool_call` driven as a raw generator — the two yielded events, the synthetic `tool_{name}_{call_id}` name (full id, see TKT-055-era fix), the timing suffix on `detail`, the `(result_text, consumed_search_slot, failed)` return triple, and that a raising tool yields a failure result with `failed=True` instead of propagating |
| `backend/tests/test_merge.py` | Artifact dedup preferring `.biom`, `_write_merged_sample_metadata` sample filtering and column union, and post-merge TSV trimming to BIOM sample IDs |
| `backend/tests/test_auth.py` | The largest file in the suite — `whoami` classification, session lifecycle, single-login behavior, route behaviour, CSRF, cross-user isolation, and the legacy `users` table migration |

Two structural notes on how the unit tier achieves independence from the outside world.

`test_agent_tool_call.py` installs stub modules for `config`, `helpers.llm_helpers`, and `helpers.agent_tools` into `sys.modules` before importing `helpers.agent`, then restores them on teardown. That is what lets an agent-loop function be tested with no client, no network, and no model.

`test_auth.py` stubs the vendored `qiita_db` / `qiita_core` packages with a fake `TRN` so that `import run` succeeds in a sandbox where the editable install is broken. It also patches `werkzeug.__version__`, which Flask 2.2.5's test client reads and upstream Werkzeug removed.

> **Gap.** Two of the three merge test classes exercise *local reimplementations* of production logic, not the shipped functions. `test_merge.py :: _run_dedup` and `_filter_tsv_to_ids` are hand-copies of the dedup loop in `helpers/qiita_fetch.py` and the TSV filter in `remote_merge.main`. They will keep passing after the originals change. Only `TestWriteMergedSampleMetadata` imports and calls the real function. Rewriting the other two against the shipped code is a contained fix.

`test_auth.py`'s module docstring refers to a `test_auth_smoke.py` "real-control-plane check". **That file does not exist in the repository.** Either it was never committed or it was removed; the reference should be dropped or the file restored.

---

## Parity testing

This is the most interesting idea in the suite, and it generalises beyond this codebase.

### The property being defended

[`04-search.md`](04-search.md) establishes that three query planners converge on **one** SQL builder, `search_studies_with_sql`. The browse box reaches it through `llm_query_to_sql`; the agent reaches it through `_collect_terms` and `expand_keyword_variants`. Two entry points, one destination.

Shared destinations drift. A change to keyword expansion, to the data-type filter, or to the parameter binding order affects both callers — but a developer testing through the browse box sees only half the blast radius. Worse, the two paths can diverge *without* either being wrong on its own terms: the agent's planner might expand a query into terms the browse planner never produces, and a study reachable through one path silently stops being reachable through the other.

The parity tests assert the property directly: **a study that a user can find through `/api/search` must also be findable through chat, and vice versa.**

### `test_search_parity.py`

Five tests over a table of `(query, expected_study_id)` cases, currently one discovery case (`"shotgun metagenomic studies on wild mice"` → `11043`) and one multi-study case (American Gut → `{16057, 2136, 1189}`).

- **2.1** — `/api/search` returns the expected study. Deterministic, no LLM.
- **2.2** — chat returns it, checked two ways: the chat's own query plan is replayed through `/api/search`, *or* the ID appears in the assistant's text. An LLM judge then confirms the answer actually addresses the query.
- **2.3** — both paths return it. This is the parity assertion proper.
- **2.4** — the chat planner's keywords are a superset of the browse planner's. Marked `xfail(strict=False)`: it **documents a relationship rather than enforcing one**, because the model does not reliably echo every input keyword. Honest use of `xfail` — a failure here is information, not a break.
- **Multi-study** — every required ID appears in the assistant's output.

### `test_chat_search_consistency.py`

The structural sibling: six tests, **no LLM anywhere**, all assertions over HTTP. Rather than asking a model to produce a plan, it hard-codes keyword lists that simulate planner output (`["wild", "mice", "shotgun", "metagenomic"]`) and pushes them through `/api/search`.

Its subject is the **visibility contract**, verified symmetrically across both paths:

- `/api/studies/<id>/detail` returns 404 for a non-public study and 200 for a public one.
- Chat-planner keyword searches find the expected study and never return the blocked one.
- Browse-planner natural-language searches do the same.

That symmetry is the point. The public-visibility constraint is expressed two different ways in the codebase — a `SELECT DISTINCT` with a visibility join in `study_service`, a correlated `WHERE EXISTS` in `qiita_fetch` ([`04-search.md`](04-search.md)) — and this file checks that both formulations exclude the same studies.

Because it avoids the model entirely, it runs in the deterministic tier. It is the parity test you can trust to fail for a real reason.

### `parity_helpers.py`

Three helpers carry both files:

- **`search_ids(backend_url, query)`** — POST `/api/search`, return a set of integer study IDs. Sets, not lists, because parity is a membership question.
- **`stream_chat(...)`** — POST to a chat stream endpoint (global or project) and consume SSE by hand, returning a dict with `search_count`, `assistant_text`, `study_ids_mentioned`, `result_study_ids`, `ui_payload`, `step_done_labels`, `pinned_studies`, and `tool_ui_payloads`. It parses agentic events (`segment_tool_result`) and preparatory `step_done` events. Study IDs in prose are recovered with a loose regex — used only as a secondary signal alongside `result_study_ids` from tool payloads.
- **`llm_judge(question, answer, rubric)`** — asks a model (kimi on NRP by default, Anthropic if a `claude-*` model is named) for a bare YES/NO. **On any exception, or a missing API key, it falls back to a regex over the answer and returns `True`.** That fail-open behaviour keeps a network outage from turning into a wall of red, at the cost of a judge test that can pass without a judge. Know that before trusting a green `--llm` run.

> **Gap.** `tests/e2e/conftest.py` overrides the autouse `fresh_db` fixture with a no-op — correct, since e2e tests never touch the local SQLite store — but its `backend` health check calls `GET /api/global-chats` **unauthenticated**, and its `global_chat` fixture passes identity as a `user_id` query parameter. Both predate the auth system. `helpers/auth_middleware.py :: PUBLIC_ENDPOINTS` contains exactly three endpoint names (`api_auth_login_url`, `api_auth_connect`, `api_auth_me`); everything else defaults to deny. Against an auth-enabled backend the health check receives 401 and **the entire e2e tier skips itself** with "barnacle backend unhealthy". `run_tests.sh` fails earlier still: its preflight `curl -sf $BARNACLE_URL/api/systems` also hits a protected endpoint, so the script aborts before collecting anything. The e2e tier needs a session-establishing fixture — connect once with a test PAT, reuse the cookie and CSRF token — before it can run again.

---

## Benchmarks as documentation

Four scripts in `backend/tests/benchmarks/`, each a plain `python script.py` against a live backend. They are not pytest tests and nothing collects them.

| Script | Measures | Method |
|---|---|---|
| `search_latency.py` | p50 and p95 across 15 representative queries | Sequential POSTs to `/api/search`, 10 s timeout each |
| `cache_bench.py` | Cold vs. warm speedup for study detail | Two `GET /api/studies/<id>/detail` calls, defaulting to study 10317 |
| `concurrent_bench.py` | p95 under 5, 10, and 15 concurrent users | `ThreadPoolExecutor` fan-out over the same query set |
| `cache_hit_rate.py` | Percentage of detail fetches served from SQLite | Replays a scripted session with repeats, reading the endpoint's own `cached` field rather than inferring from timing |

`cache_hit_rate.py` deserves the credit: trusting the server's reported `cached` flag instead of guessing from latency is what makes its number meaningful.

**These scripts produced TKT-024's measured range.** The "86 ms to 13.5 s" figure cited in [`04-search.md`](04-search.md) came from `search_latency.py` and `concurrent_bench.py` on 2026-07-04, along with the observation that 3 of 15 representative queries exceeded the 10 s timeout outright. They are the evidence behind the diagnosis, not an estimate — which is exactly what benchmarks are for.

> **Gap.** Benchmarks are documentation, not assertions. **Nothing fails if performance regresses.** There is no baseline file, no threshold, no CI invocation — a change that doubles search latency produces no signal until someone re-runs the scripts by hand and remembers what the old numbers were. Recording a committed baseline and adding a pytest wrapper that fails on a large regression would convert four measurement tools into four regression guards. They also hard-code `BASE = "http://localhost:5001"` rather than reading `BARNACLE_URL`, so they cannot be pointed at a remote backend without editing the source, and — like the e2e tier — they call protected endpoints without a session.

---

## TKT-041 — the fixture isolation leak

`backend/tests/conftest.py :: fresh_db` is `autouse=True` and intended to give every test its own SQLite file. For a specific class of test it does. For route-level tests it does not, and the mechanism is worth understanding precisely, because the same trap recurs anywhere a module-level constant is read from the environment.

### What the fixture does

Three steps: set `QIITA_EXPERIMENT_DB_PATH` to a `tmp_path` via `monkeypatch.setenv`; delete every `sys.modules` entry whose **name contains the substring** `'store'` or `'sql_store'`; re-import `store.db` and confirm the schema exists.

### Why the re-import is needed at all

Two facts about `backend/store/db.py` combine into the problem:

```python
DB_PATH = os.getenv("QIITA_EXPERIMENT_DB_PATH", os.path.join(_DEFAULT_DATA_DIR, "projects.db"))
...
_bootstrap()   # last line of the module — runs at import
```

`DB_PATH` is a **module-level constant evaluated once**, at first import. `_conn()` closes over it. And `_bootstrap()` — which creates the schema and applies the `ALTER TABLE` migrations — is called at the bottom of the module, so **merely importing `store.db` opens and migrates whatever `DB_PATH` currently names**. Setting the environment variable after import has no effect whatsoever. Hence the `sys.modules` surgery: deleting the entry forces a fresh module object that re-reads the variable.

### Why route-level tests bypass it

Deleting a name from `sys.modules` does not invalidate references that other modules already hold.

Every route module binds store functions **by name, at its own import time**:

```python
# routes/chat_routes.py
from store import get_study_detail_cache, upsert_study_detail_cache, pin_study_to_chat, ...
```

Those are function objects, and each one closes over the `store.db` module object that existed when it was defined — with that module's original `DB_PATH`. Removing `store` and `store.db` from `sys.modules` makes the *next* import produce a new module; it does nothing to the old module object, which stays alive because `routes.chat_routes` still references functions bound to it.

And the route modules themselves are never purged, because the filter is a substring test on the module name: `'store' in 'routes.chat_routes'` is `False`. Nor is `run`. So after the first import in a session, `routes.*` and `run` keep their stale bindings for the rest of the run.

The result is a split brain:

| Test style | Binds to | Writes to |
|---|---|---|
| `crud` / `db_conn` / `cache` fixtures — import the module *after* the purge | The fresh `store.db` | The fixture's `tmp_path` ✅ |
| `app.test_client()` — goes through already-imported `routes.*` | The original `store.db` | **The developer's real `backend/data/projects.db`** ❌ |

This was confirmed on 2026-07-06: a run of `pytest tests/ --ignore=tests/e2e` applied the pending `study_detail_cache` migrations (`prep_metadata_json`, `samples_json`, `total_samples`) to the real local `projects.db`. That instance was harmless — additive columns, no row data touched, verified with a full `sqlite3 .dump` diff — but the same path would let a route-level test *write fixture rows* into a real database.

`test_auth.py` is the only file that currently drives routes, and it works around the leak rather than fixing it: its module-scoped `_app` fixture purges `run`, `routes.*`, and `store*` **before** importing `run`, and sets the env var with a direct `os.environ` assignment. A side effect is visible inside every auth test — the autouse `fresh_db` still runs and points a *fresh* `store.db` at a different temp file, so `TestAuthStore` (direct store imports) and `TestAuthRoutes` (through the app) operate on two different SQLite files in the same test function. Nothing currently depends on them being the same, which is why this has not bitten.

### The fix

Make the path lazy rather than frozen. Replace the module-level constant with an accessor read at call time:

```python
def _db_path():
    return os.getenv("QIITA_EXPERIMENT_DB_PATH", os.path.join(_DEFAULT_DATA_DIR, "projects.db"))

def _conn():
    conn = sqlite3.connect(_db_path())
    ...
```

Then move `_bootstrap()` off the import path — call it lazily on first connection, keyed by path — so importing the module no longer touches a database. With both changes, `monkeypatch.setenv` alone is sufficient and the `sys.modules` deletion loop can be removed entirely, along with its over-broad substring filter (which would also purge any third-party module with `store` in its name).

Add the regression test the ticket asks for: run a route-level write through `app.test_client()`, then assert the real `backend/data/projects.db` is unchanged.

---

## Coverage gaps, stated honestly

### The frontend has zero automated tests

Verified by searching the repository for `package.json`, `jest.config*`, `vitest.config*`, `playwright.config*`, `cypress.config*`, `karma.conf*`, any `*.test.js` / `*.spec.js` file, and any `__tests__` directory. **None of these exist anywhere.** There is no JavaScript test tooling and no JavaScript test.

This follows from the architecture — the frontend is React via Babel standalone with no build step ([`08-frontend.md`](08-frontend.md)), so there is no bundler to hang a test runner off. It is a consequence, not an oversight, but the consequence is that `app_state.js`, `components.js`, and `utils.js` are entirely unguarded.

### The SSE segment contract has no test — this is the headline

[`06-streaming-and-chat.md`](06-streaming-and-chat.md) documents the dual-authoring hazard: the segment array is built **twice, independently, in two languages**. The server builds it in `routes/global_chat_routes.py` and persists it to `ui_payload`; the client builds it in `app_state.js` and freezes it into `m.ui`. The live view comes from one, the reloaded view from the other. They agree today only because someone checked field by field.

They are also not semantically identical: the server completes the **first** matching tool segment and breaks; the client's `.map()` completes **every** match. What makes those equivalent is an invariant elsewhere — the synthetic name carries a call-id suffix, so no two live segments share a name. Nothing tests that invariant either.

> **Gap.** No automated check exists that the two constructions agree. This is the single highest-value test the repository lacks, and it is cheap.

A concrete sketch:

1. Record a realistic event sequence from `stream_agent` — an interleaved run of `agent_start`, `token`, `segment_tool_call`, `segment_tool_result`, `token`, `done` — and commit it as a JSON fixture. Capture it once from a live agentic turn via `backend/agent_harness.py`, which consumes the same generator.
2. Feed the fixture to the server's reducer. Today that logic is inline in the route handler, so the test's first act is to **extract it into a callable** — `build_segments(events) -> list` — which is a refactor worth doing on its own.
3. Feed the same fixture to a Python port of the client's four handlers (`onAgentStart`, `onTokenAgent`, `onSegmentToolCall`, `onSegmentToolResult`) plus `applyStreamDone`. Keep the port short and structurally faithful — including the `.map()`-over-all-matches semantics, so the asymmetry is *represented* rather than smoothed over.
4. `assert server_segments == client_segments`, deep equality over the whole structure including `args`.
5. Add a second case with **two open tool segments sharing a name**, and assert the two implementations diverge. That test documents the latent asymmetry and turns green the day someone makes the client `break` on first match.

It would live at `backend/tests/test_segment_parity.py`, in the unit tier. It needs no database, no PostgreSQL, no LLM, and no browser — the only thing it fakes is the event stream, and that comes from a committed fixture. The maintenance cost is real and should be stated: the Python port must be updated whenever `app_state.js` changes. That is the same discipline the checklist in [`06-streaming-and-chat.md`](06-streaming-and-chat.md) already demands, with the difference that a test fails loudly and a checklist does not.

### The merge executor is untested on remote

TKT-015: the executor shipped to master in dev-only mode, running a local `conda run` with a "before merging to master" TODO still in place. `test_merge.py` covers metadata assembly and TSV shaping with mocked fetches; **nothing exercises the execution path**, and no test tier requires a conda environment. The remote deployment failure mode is unverified in either direction.

### Auth coverage — better than expected

`test_auth.py` is the strongest file in the suite, and the paths one would guess are missing are in fact covered:

| Path | Covered? |
|---|---|
| Single-login — `whoami` called only at connect, not on `/auth/me` or protected routes | Yes — `TestSingleLogin::test_whoami_called_only_at_connect` |
| Single-login — PAT not persisted in SQLite | Yes — `TestSingleLogin::test_connect_does_not_persist_pat` |
| Single-login — legacy `pat_encrypted` ciphertext scrubbed on bootstrap | Yes — `TestSingleLogin::test_legacy_pat_ciphertext_scrubbed_on_bootstrap` |
| Single-login — successful re-login replaces prior session; failed re-login preserves current | Yes |
| CSRF — missing token 403, wrong token 403, correct token 200 | Yes |
| Session lifecycle — revoke, absolute expiry, and that `touch` does not extend absolute expiry | Yes |
| `whoami` classification — human accepted; service and anonymous rejected; 401 non-transient; 5xx and `ConnectError` transient | Yes |
| Cross-user isolation — projects, global chats, merge-workspace mutations, and client-supplied `user_id` spoofing ignored | Yes |
| Legacy pre-auth `users` table migration, and that a fresh DB is a no-op | Yes |

What is **not** covered: the session-cookie attribute set (`Secure`, `HttpOnly`, `SameSite`) is never asserted — and the fixture forces `SESSION_COOKIE_SECURE = False`, so a regression flipping it off in production would pass; the legacy-claim **race** path (`claim_legacy_default` reaching its DB-level conflict rather than the `claim_eligible()` guard) is explicitly noted as unreached by the existing test; `PUBLIC_ENDPOINTS` is checked for one entry (`api_auth_login_url`) rather than asserted as an exact set, so an endpoint accidentally added to it would go unnoticed; and every `whoami` in these tests is a fake — the real control-plane handshake is exercised only by the `test_auth_smoke.py` the docstring names and the repository does not contain.

### Other holes worth naming

- **No test for the parameter binding order in `search_studies_with_sql`.** [`04-search.md`](04-search.md) states plainly that getting `score_params + dt_params + params` wrong produces **no error** — keywords bind into the data-type filter and the query returns confidently wrong results. A pure unit test that builds the SQL and asserts the parameter list's composition needs no database at all.
- **No test for partial results on sample-search timeout.** The `as_completed` budget in `helpers/sample_search.py` degrades recall rather than failing the request. That a timeout yields fewer studies and not an exception is a behavioural contract nothing checks.
- ~~No test for the search-tool gating.~~ Resolved: the gate is now a budget of `SEARCH_CALLS_PER_MESSAGE` (5) executed searches, and `tests/agent/test_search_budget.py` covers both halves — the `consumed_search_slot` return (`TestBudgetAccounting`: executed / empty-input / crashed / non-search / past-the-cap short-circuit) and the schema consequence (`TestBudgetInTheLoop`: the search tools are stripped from the schema after the budget is spent, and never burned by an empty-input call).
- **No test for forced synthesis.** That a loop ending on a tool message triggers a no-tools re-call, so the user never sees an empty turn, is untested.
- **Project chat** has unit tests (`test_project_scope.py`) and deterministic e2e coverage (`test_journey_project_chat.py`, `test_journey_pinning.py` project scope). LLM-judge project-agent tests remain in the `--llm` tier.
- **No CI.** No workflow file invokes any tier. Every run is manual.

---

## Running the suite

```bash
cd qiita_explore/backend
pip install -r tests/requirements.txt

bash run_tests.sh --unit     # unit only — no backend needed
bash run_tests.sh            # unit + deterministic e2e
bash run_tests.sh --llm      # + the LLM-judge subset
bash run_tests.sh --all      # everything
```

`run_tests.sh` runs from its own directory (`cd "$SCRIPT_DIR"`), so it works from anywhere. `BARNACLE_URL` overrides the backend address, defaulting to `http://localhost:5001`.

The `--unit` flag is the one to know: it is checked **before** the backend health probe, so it is the only mode that skips the preflight entirely. Every other mode aborts if `curl -sf $BARNACLE_URL/api/systems` fails — which, per the gap noted above, it currently does against an auth-enabled backend.

**What to expect with nothing else running.** All 95 unit tests pass in roughly a second; the README's "~0.3s" predates the auth and merge files. `test_auth.py` includes two `time.sleep(1.2)` calls for TTL expiry, so about 2.5 s of the runtime is deliberate waiting. Nothing requires PostgreSQL, an LLM, or network access. Under bare `pytest tests/` (without `-m "not e2e"`), the e2e directory is collected and each test skips at its `backend` fixture — noisy but harmless.

**Fixtures available to a new unit test**, all in `backend/tests/conftest.py`:

| Fixture | Provides |
|---|---|
| `fresh_db` (autouse) | A temp SQLite path with schema created — subject to TKT-041 above |
| `db_conn` | A raw `sqlite3` connection for direct assertions |
| `crud` | `store.crud`, imported after the purge |
| `global_chat_crud` | `store.global_chat_crud`, split out to stay under the 500-line cap |
| `sample_user_id` | `"test_user_001"` |
| `sample_study` | A study dict with the fields the store expects |

If a new test needs the Flask app, copy `test_auth.py`'s `_app` pattern rather than relying on `fresh_db` — until TKT-041 is fixed, that is the only way to keep route-level writes off the real database.

---

*See also: [`06-streaming-and-chat.md`](06-streaming-and-chat.md) for the contract the missing parity test would guard · [`04-search.md`](04-search.md) for the invariants search tests should cover · [`09-operations.md`](09-operations.md) for running the backend the e2e tier needs · [`11-roadmap.md`](11-roadmap.md) for where these gaps sit against other work.*
