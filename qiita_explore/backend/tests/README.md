# qiita_explore Test Suite

Two layers:

1. **Unit tests** (`tests/*.py`) — CRUD operations, data integrity, schema
   correctness. Run against a temp SQLite DB, no backend or external
   services required.
2. **E2E tests** (`tests/e2e/`) — real user journeys against a *live*
   barnacle backend, as a real logged-in Qiita user: login, chat creation,
   project chats, studies in a project, studies reaching the LLM's context,
   and pinning.

## Unit tests

```bash
cd qiita_explore/backend
pip install -r tests/requirements.txt
python -m pytest tests/ -m "not e2e" -v
```

| File | Coverage |
|------|----------|
| `test_crud.py` | Project CRUD, user isolation, ordering |
| `test_studies.py` | Add/remove studies, deduplication |
| `test_chats.py` | Chat CRUD, message persistence |
| `test_api.py` | Schema integrity, foreign keys, cascade delete |
| `test_auth.py` | Classic-Qiita login (every check in order), `resolve_user` (one account per email, matched by email only; 409 for an email that moved between qiita-miint principals), sessions, the Qiita re-check, CSRF, cross-user isolation, TKT-042 |
| `test_login_limiter.py` | Login delay / known device / per-client block; email and password rules; bcrypt check; circuit breaker |
| `test_miint_connect.py` | qiita-miint login beside classic: the `whoami` client (httpx faked; circuit breaker, per-worker busy slot), `POST /api/auth/connect` (token shape, front checks, 401/503/502, email rule and padded emails, 409 for an email that moved to another principal, busy 503, failed connect keeps the session, token never stored), `GET /api/auth/login-url` (with and without LoginRocket), one account per email across both logins, `QIITA_EXPLORE_LOGIN_METHODS` switches (routes 404, sessions signed out, bad values and a missing / plain-HTTP `QIITA_CONTROL_PLANE_URL` failing at import), and which sessions are re-checked (classic) or not (`qiita-miint`, legacy `paste`) — a failed classic re-check ends the account's qiita-miint sessions too |
| `test_users_migration.py` | The `users` migration to one account per email (`_migrate_users_to_email_v1`) from both earlier shapes, qiita-miint (production) and classic_v1: emails kept and normalized, a shared email kept by the classic-bound / latest-login row and the rest (and unusable emails) logged, sessions not revoked, both logins reaching migrated accounts; plus crash before `COMMIT`, two workers migrating once, a second boot changing nothing, and the pre-auth table still moved aside |
| `test_artifact_access.py` | TKT-043: artifact files and samples only for public artifacts of public studies |
| `test_pinned_context.py` | Pinned-study context builder (inline vs. manifest, budget) |
| `test_agent_tool_call.py` | Agent tool-call dispatch |
| `test_merge.py` | Merge workspace logic |

Tests use isolated temporary SQLite databases (one per test via the `fresh_db`
fixture) and stub `qiita_db`/`qiita_core` — no external dependencies. Fast
(~seconds for the full suite).

## E2E tests — overnight full suite

Every non-auth endpoint requires a real session (`helpers/auth_middleware.py`
is default-deny), so the e2e suite logs in once via `POST /api/auth/login` as a
real classic-Qiita account and runs every journey as that user (it doesn't
use the qiita-miint login). Use a test account: the password sits in your
shell environment for the run. Route tests that need a session sign in
through the real routes instead — `POST /api/auth/login` with the account
table faked (`login()`), or `POST /api/auth/connect` with `whoami` faked
(`connect()`, `fake_whoami()`, `token_for()`, `human()`, `post_connect()`) —
all in `tests/auth_helpers.py`. `tests/conftest.py` sets a dummy `https://` `QIITA_CONTROL_PLANE_URL` so `config` imports with the qiita-miint login on; `whoami` itself is always faked.

**Run it with the single overnight entrypoint, not pytest directly:**

```bash
export QIITA_TEST_EMAIL=<a verified classic-Qiita test account>
read -rs QIITA_TEST_PASSWORD && export QIITA_TEST_PASSWORD   # not in shell history
export OPENAI_API_KEY=<key>   # needed for llm_judge — see warning below
bash qiita_explore/start_barnacle.sh &   # or confirm the systemd unit is up

bash qiita_explore/run_full_suite.sh              # full run, LLM phase included
bash qiita_explore/run_full_suite.sh --no-llm      # fast pass, deterministic only
bash qiita_explore/run_full_suite.sh --unit        # also run the unit suite first
bash qiita_explore/run_full_suite.sh --keep        # skip teardown, inspect leftovers
bash qiita_explore/run_full_suite.sh --model NAME  # run LLM-touching turns against a specific model
```

Required/optional env vars:

| Var | Required | Purpose |
|-----|----------|---------|
| `QIITA_TEST_EMAIL` / `QIITA_TEST_PASSWORD` | yes | classic-Qiita account the suite logs in and runs as |
| `BARNACLE_URL` | no (default `http://localhost:5001`) | backend base URL |
| `QIITA_TEST_ORIGIN` | no (default `http://localhost:5503`) | `Origin` header for `/api/auth/login`; must equal the backend's `QIITA_EXPLORE_PUBLIC_ORIGIN` or one of `QIITA_EXPLORE_ALLOWED_ORIGINS` |
| `QIITA_TEST_ALLOW_FAILED_LOGIN` | no | `1` runs the wrong-password e2e test — dev backend only, since failed attempts count toward the account's login delay |
| `OPENAI_API_KEY` / `API_KEY` | no, but see below | used by the `llm_judge()` helper |

**⚠ Without `OPENAI_API_KEY`/`API_KEY` set, `llm_judge()` silently returns
`True` for every judge assertion** (`parity_helpers.py`) — the preflight
step warns loudly about this, but any Phase 2 "pass" in that state cannot be
trusted as a real result.

### Reading the summary

`run_full_suite.sh` logs to `qiita_explore/logs/full_suite_<timestamp>.log`
(plus per-phase logs) and prints a SUMMARY block at the end:

- **Phase 1 (deterministic)** — pure HTTP + Postgres assertions (auth, CRUD,
  pin outcomes, cap/dedup, context step counts). A failure here is a **real
  regression** — the script exits non-zero on Phase 1 failure specifically.
- **Phase 2 (LLM-touching)** — turns that go through the live LLM, including
  `llm_judge()` quality checks. A failure here **may be model variance**, not
  a regression — read the log before treating it as a break.

The suite tags everything it creates with a run-specific `E2E-<timestamp>`
prefix and tears it down afterward (unless `--keep`); the account you run it
as accumulates no leftover projects/chats from a normal run.

### Test files

| File | Journey |
|------|---------|
| `test_journey_auth.py` | login, session cookie + CSRF, logout |
| `test_journey_projects.py` | projects, studies in a project |
| `test_journey_pinning.py` | pin/unpin, cap (10/chat), dedup, invalid studies |
| `test_journey_global_chat.py` | global chat creation + messaging |
| `test_journey_project_chat.py` | project chat creation + messaging |
| `test_journey_studies_in_context.py` | studies reaching the LLM's context (deterministic + LLM-judge) |
| `test_search_parity.py`, `test_blocked_studies.py`, `test_chat_search_consistency.py`, `test_deepsearch.py` | search/visibility parity carried over from the pre-auth suite |

`e2e` tests must always pass. `e2e_llm` tests additionally go through a live
LLM call — some assert only on deterministic backend signals (step names,
counts) despite the marker name; only tests that judge the LLM's actual
output quality are truly variance-prone.