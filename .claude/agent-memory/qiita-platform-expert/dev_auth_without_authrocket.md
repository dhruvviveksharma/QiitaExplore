---
name: dev-auth-without-authrocket
description: How to get a working qk_ PAT against a locally-run Qiita control plane without a real AuthRocket tenant — the practical path for qiita_explore/qiita-web devs to test integration
metadata:
  type: reference
---

Verified 2026-07-12 against commit `837fc6cd`. The control plane (FastAPI, `qiita-control-
plane/`) boots and serves fine with zero AuthRocket env vars set — `.env.control-plane.
example` (comment block "AuthRocket (optional in dev; required in prod)") states plainly:
"The CP boots without these set, but any OIDC-bearing request will fail until they are
configured. Dev environments that exercise the auth path typically use the stubbed JWKS
harness in tests rather than a real realm." There is no runnable "fake AuthRocket server"
shipped as a dev tool outside the test suite — the two real options are:

**Option A — mint a PAT directly, skip OIDC entirely (recommended for qiita-web dev).**
`mint_api_token()` (`qiita-control-plane/src/qiita_control_plane/auth/token.py:57-101`) is a
plain async function: `mint_api_token(pool_or_conn, *, principal_idx, label, scopes,
expires_at=None) -> (plaintext, token_idx)`. It has no dependency on OIDC/AuthRocket at
all — it just inserts a row into `qiita.api_token` keyed to an existing `principal_idx`.
This is exactly what the pytest session fixtures do
(`qiita-control-plane/src/qiita_control_plane/testing/sessions.py` —
`human_admin_session`, `regular_user_session`, `wet_lab_admin_session`,
`compute_worker_service_account`) and what the reusable seed helpers do
(`qiita-control-plane/src/qiita_control_plane/testing/db_seeds.py` —
`seed_user_principal`, `seed_service_principal`). Recipe for a one-off dev script (needs
direct Postgres access to whatever DB backs the local control plane — this is a dev-only
bypass, never usable against the production DB):
  1. Run migrations (`dbmate up`) against a local Postgres, per `qiita-control-plane/
     README.md`.
  2. Insert a `qiita.principal` row (`system_role` = desired role) + a `qiita.
     service_account` (for a ServiceAccount) or `qiita.user` (for a HumanUser, needs
     `affiliation`/`address`/`phone` filled for `profile_complete`) row — mirror
     `seed_service_principal`/`seed_user_principal` in `db_seeds.py`.
  3. Call `mint_api_token(pool, principal_idx=..., label=..., scopes=[...])` — scopes must
     be valid `Scope` enum values (`qiita_common.auth_constants.Scope`); a ServiceAccount's
     scopes are capped at `SERVICE_ACCOUNT_SCOPE_CEILING`.
  4. Use the returned plaintext (`qk_...`) as the `Authorization: Bearer` value.
  This requires the Qiita monorepo Python env installed locally (`uv sync` in
  `qiita-control-plane/`) so `qiita_control_plane.auth.token` is importable — feasible for
  a qiita-web dev doing local integration testing, not something qiita-web ships.

**Option B — exercise the full OIDC/handoff flow with a fake IdP (heavier, only useful
  if testing the login UX itself, not just downstream API calls).**
`JwksHarness` (`qiita-control-plane/src/qiita_control_plane/testing/jwks.py`) is an
in-process HTTP server that serves a JWKS document and signs arbitrary-claim JWTs
(including a synthetic `auth_time`, which the real AuthRocket realm does NOT emit — see
[[oidc-pat-flow-gotchas]]). Point `AUTHROCKET_JWKS_URL`/`AUTHROCKET_ISSUER` at it and you
can drive `/auth/login` → (skip the real AuthRocket UI, forge a token) → `/auth/handoff`
end-to-end. But `JwksHarness` only signs tokens — it does NOT serve AuthRocket's hosted
login UI, so nothing actually redirects a real browser through it outside a test process
driving the ASGI app directly (`test_auth_endpoints.py` uses `AsyncClient` against
`ASGITransport(app=app)`, not a real running server + browser). Not practical as a
"real running local server" dev harness; Option A is the practical one for qiita-web.

**Bootstrap chicken-and-egg for `POST /admin/service-account`:** that route requires a
bearer that already carries `admin:service_accounts` scope. The only way to get the very
first admin-scoped token without AuthRocket is Option A (direct DB seed + `mint_api_token`
against a `system_admin`-role principal) — there is no seeded bootstrap admin in the
migrations (checked `DEPLOY_CHECKLIST.md`, no `INSERT INTO qiita.principal` bootstrap
step found for first-admin creation outside the runbook's own AuthRocket-driven first
login).
