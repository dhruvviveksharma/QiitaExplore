# Qiita Authentication Integration Plan

> How to integrate the **new Qiita platform** (`/Users/dhruvsharma/Downloads/Projects/Qiita`)
> user-authentication method into qiita_explore, to enable user-specific accounts,
> chats, and merges.

Investigation performed 2026-07-06 by fanning out three read-only
`qiita-platform-expert` subagents over the Qiita control-plane source. Every
endpoint path, scope name, and token shape below traces to a cited file under
`/Users/dhruvsharma/Downloads/Projects/Qiita`.

---

## The core finding: the two sides are almost perfectly complementary

**qiita_explore today** threads a `user_id` through every table (`projects`,
`project_chats`, `global_chats`, `merge_workspaces`, `merge_jobs`) and every
route, but it's a fake — `user_id` defaults to `"default"` everywhere, the
frontend hardcodes `const USER_ID = 'default'`, and there's no login UI, no
session, no token verification. There *is* a local `users` table
(`user_id, username, email, password_hash, role`) with three seeded users
(`admin`, `dhruv`, `tester`), but nothing reads it for auth.

**Qiita** has a complete, production-grade auth system but **zero concept of
projects, chats, or merges** — confirmed by directory listing and route
search (`routes/` has `reference`, `study`, `biosample`, `prep-sample`,
`sequencing-run`, `sequenced-sample`, `work-ticket`, `upload`, `auth`,
`admin`, `user` — no chat/workspace/merge). Qiita's user surface is just
`GET/PATCH /api/v1/user/me` (profile) plus its data endpoints. There is no
`GET /study` list endpoint — only by-idx reads (`routes/study.py:192`).

**This means:** projects, chats, and merges stay 100% SQLite-local in
qiita_explore. Qiita doesn't have equivalents to migrate to. What Qiita *does*
provide is the **identity layer** (who is the user?) and the **data layer**
(study/biosample reads via REST + Arrow Flight). The integration is: adopt
Qiita's principal model as qiita_explore's user identity, and key all local
SQLite tables by Qiita's `principal_idx` instead of the hardcoded `"default"`.

---

## Recommended integration architecture (two layers)

### Layer 1 — End-user identity via Qiita's AuthRocket OIDC flow

qiita_explore's users should *be* Qiita human principals. On login:

1. **`GET /api/v1/auth/login`** (`routes/auth.py:380`) — Qiita sets an
   HMAC-signed cookie (`qiita_login_state`, 300s TTL, `HttpOnly`+`Secure`+
   `SameSite=Lax`) and 302-redirects to AuthRocket's LoginRocket Web UI with
   `redirect_uri=<qiita>/auth/handoff&prompt=login`. The `prompt=login` forces
   interactive re-auth even when AuthRocket has a cached browser session.
2. **AuthRocket** — user enters credentials at AuthRocket's hosted UI;
   AuthRocket validates and issues an RS256 JWT with `iss`, `sub`, `email`
   claims.
3. **`GET /api/v1/auth/handoff?token=<JWT>`** (`routes/auth.py:488`) — Qiita
   verifies the signed cookie freshness, verifies the JWT (JWKS, RS256,
   claims: `exp`/`iss`/`sub` required, `email` must be non-empty), and
   **upserts the principal + user row** (`auth/principal.py:292-362`). First
   login for a new `(issuer, subject)` pair creates rows with
   `system_role=USER`. Then Qiita mints a PAT (opaque `qk_...` token,
   SHA-256 hashed in DB) scoped to the user's role ceiling.
4. **Browser**: HTML page renders the PAT plaintext once. **CLI**: redirects
   to `127.0.0.1:<port>/?ot_code=<plaintext>`; CLI POSTs to
   `/api/v1/auth/cli-exchange` to redeem.

There are **two integration options** for how qiita_explore consumes this:

| Option | How it works | Trade-off |
|--------|-------------|-----------|
| **A. qiita_explore proxies Qiita auth** (recommended) | qiita_explore frontend hits its own `/auth/login`; qiita_explore backend redirects to Qiita's `/auth/login`, captures the PAT from `/auth/handoff`, stores it in a server-side session mapped to `principal_idx`. All subsequent qiita_explore API calls carry the session cookie. | qiita_explore owns the session; the PAT never touches the browser. More glue but a cleaner security boundary. |
| **B. qiita_explore frontend talks to Qiita directly** | Frontend redirects to Qiita `/auth/login` directly, gets the PAT from the handoff page, stores it in memory/localStorage, and sends `Authorization: Bearer qk_...` on every qiita_explore API call. qiita_explore backend verifies the PAT by calling Qiita's `GET /auth/whoami`. | Less backend state, but the PAT lives in the browser (XSS risk) and qiita_explore must proxy-verify each request. |

**Recommendation: Option A.** The PAT is a long-lived bearer credential (up
to 365 days for humans, `auth_constants.py:177`); it should never reach the
browser. qiita_explore's backend holds the PAT server-side in a session and uses
it (or a backend ServiceAccount PAT — see Layer 2) to make Qiita data calls
on the user's behalf.

### Layer 2 — Backend-to-Qiita calls via a ServiceAccount PAT

qiita_explore's Flask backend needs to call Qiita REST/Flight APIs to fetch study
metadata, biosamples, and prep-samples for chat context and merge validation.
The clean credential for this is a **ServiceAccount PAT**:

- **Provision once (out-of-band):** a human operator with `system_admin` role
  + `admin:service_account` scope calls
  `POST /api/v1/admin/service-account` (`routes/admin.py:81`). Body:
  `{ name: "qiita_explore-backend", scopes: [...], ttl_days? }`. The response
  (`ServiceAccountCreateResponse`, `models.py:1361`) contains the plaintext
  `token` field — **shown only here, never stored**. The operator captures it
  and writes it to a path like `/etc/qiita/qiita_explore.token` (mode 0400).
- **Scope set for the backend SA:** `reference:read` (read reference
  sequences/taxonomy), `ticket:doget` (mint Arrow Flight DoGet tickets for
  reading data), `study:read` (read study metadata by idx), `biosample:read`,
  `prep_sample:read`. Add `ticket:doput` only if qiita_explore ever uploads data
  back to Qiita. The SA scope ceiling is flat
  (`SERVICE_ACCOUNT_SCOPE_CEILING`, `auth/scopes.py:101-115`), so each scope
  must be explicitly listed.
- **Verification:** Qiita's `get_current_principal` dependency
  (`auth/principal.py:158-199`) resolves `Authorization: Bearer qk_...` →
  `verify_api_token` (SHA-256 hash lookup) → returns a `ServiceAccount`
  principal with the granted scopes. Rejection (missing/revoked/expired
  token, disabled principal) → 401.
- **Reference client template:** `qiita-common/src/qiita_common/client.py` —
  `ControlPlaneClient` sets `Authorization: Bearer {token}` once at
  construction. It's async-only (`httpx.AsyncClient`); qiita_explore's Flask is
  synchronous, so mirror it with a synchronous `httpx.Client`.

---

## Concrete integration: user-specific accounts, chats, merges

### Accounts — replace fake `"default"` user with Qiita `principal_idx`

Since qiita_explore will adopt Qiita's identity layer:

- **Add a `principal_idx` column** to the local `users` table (and/or a
  `qiita_principal_idx` foreign-key concept). On first login via the Qiita
  OIDC flow, qiita_explore inserts a local `users` row keyed by the Qiita
  `principal_idx` returned from `GET /auth/whoami`
  (`WhoAmIHumanResponse`: `kind`, `principal_idx`, `email`, `system_role`,
  `scopes`, `profile_complete`).
- **Migrate all tables from `user_id` (string) to `principal_idx` (int FK).**
  This is the biggest mechanical change: `projects.user_id`,
  `project_chats.user_id`, `global_chats.user_id`, `merge_workspaces.user_id`,
  `merge_jobs.user_id`, `project_context_summaries`, `chat_pinned_studies`.
  The schema already indexes by `(user_id, updated_at)` — swap the column.
- **Drop `password_hash`** from the local `users` table — passwords are now
  AuthRocket's problem, not qiita_explore's. qiita_explore becomes passwordless.
- **The seeded `admin`/`dhruv`/`tester` users** become Qiita principals
  provisioned via `POST /api/v1/admin/user` (system_admin onboarding path) or,
  more naturally, via first OIDC login.

### Chats — stay SQLite-local, keyed by `principal_idx`

- Both project chats (`project_chats`, `project_chat_messages`) and global
  chats (`global_chats`, `global_chat_messages`) stay exactly where they are.
  Qiita has no chat concept to integrate with.
- The only change: replace
  `user_id = request.args.get('user_id') or 'default'` (in `chat_routes.py:39`,
  `global_chat_routes.py:39`, etc.) with resolution of the authenticated
  principal from the session: `principal_idx = session['principal_idx']`.
- The chat *context* — `_build_project_study_context` and
  `_build_global_search_context` — currently reads Qiita study data over
  direct PostgreSQL (`qiita_db.sql_connection.TRN`). When integrating, those
  direct SQL reads get replaced with REST/Flight calls authenticated via the
  backend ServiceAccount PAT (Layer 2). This is the TKT-007 refactor surface.

### Merges — stay SQLite-local, keyed by `principal_idx`

- `merge_workspaces`, `merge_jobs`, and the merge CRUD
  (`store/merge_crud.py`) stay local. Qiita has no merge/workspace concept
  (confirmed: no chat/workspace/merge routes).
- Same change as chats: `merge_routes.py:65` `_user_id()` and
  `merge_routes.py:123` `_DEFAULT_USER` get replaced with authenticated
  `principal_idx` resolution.
- The merge *executor* (`helpers/merge_executor.py`) currently runs local
  `conda run` — this is the TKT-015 "dev-only mode, will fail on remote
  deploy" surface. Integrating with Qiita doesn't change this; it's
  orthogonal.

---

## What stays local vs. what calls Qiita

| Concern | Local (SQLite) | Calls Qiita |
|---------|---------------|-------------|
| **Identity** | local `users` row mirrors Qiita principal | `GET /auth/whoami`, `GET /auth/login` → OIDC handoff |
| **Projects** | `projects`, `project_studies` | study metadata reads via REST (`GET /study/{idx}`) |
| **Chats** | all chat tables + messages | study context fetch via REST/Flight (SA PAT) |
| **Merges** | `merge_workspaces`, `merge_jobs` | validation reads via REST/Flight (SA PAT) |
| **Sessions** | session cookie → `principal_idx` | PAT verification via `whoami` (if proxying) |

---

## Sequenced rollout (lowest-risk first)

1. **Add auth middleware to qiita_explore backend** — a Flask `before_request`
   hook that resolves the session's `principal_idx` and makes it available to
   routes. No behavior change yet; routes still default to `"default"` if no
   session.
   → *verify: hit any route without a session, still works.*
2. **Stand up the Qiita control plane** (if not already running) and
   provision the `qiita_explore-backend` ServiceAccount via
   `POST /admin/service-account`. Store the `qk_` token at
   `/etc/qiita/qiita_explore.token`.
   → *verify: `curl -H "Authorization: Bearer <token>" <qiita>/api/v1/auth/whoami` returns `{"kind":"service",...}`.*
3. **Build a `QiitaClient`** in `qiita_explore/backend/helpers/qiita_client.py`
   mirroring `ControlPlaneClient` (sync httpx, reads token from path/env).
   Add `whoami()` and `get_study(idx)` methods.
   → *verify: call `whoami()` from a Python REPL, get the SA shape.*
4. **Add the OIDC login route** to qiita_explore: `/auth/login` redirects to
   Qiita's `/auth/login`; `/auth/handoff` captures the PAT from Qiita's
   handoff response and stores `principal_idx` + PAT in a server-side session
   (http-only cookie).
   → *verify: full login round-trip in browser, session cookie set.*
5. **Migrate SQLite tables from `user_id` to `principal_idx`.** Add the
   column, backfill existing rows (map `"default"` → a real provisioned
   principal or a local-only admin), drop `password_hash`, update all
   indexes. Update `store/crud.py`, `store/merge_crud.py`, `store/cache.py`,
   and all routes to resolve `principal_idx` from the session instead of
   `request.args.get('user_id')`.
   → *verify: log in as two different users, each sees only their own projects/chats/merges.*
6. **Replace direct-Postgres study reads with REST/Flight calls** via the
   backend SA PAT (the TKT-007 surface). This is the largest change and can
   proceed incrementally — one `_build_*_context` helper at a time.
   → *verify: chat context still populated, now via Qiita REST instead of direct SQL.*

---

## Key gaps and risks to flag

1. **No "list my studies" endpoint on Qiita.** Studies are read by known idx
   (`GET /study/{idx}`, `routes/study.py:192`). If qiita_explore needs "show me
   all studies I own," it must either (a) maintain its own mapping from
   `principal_idx` to owned study idxs locally, or (b) a future Qiita
   endpoint would need to be built. qiita_explore's browse grid
   (`GET /api/studies/first`, `POST /api/search`) currently reads from the
   classic Qiita monolith's direct Postgres — that path is *not* the new
   platform's surface and is the TKT-007 refactor target.
2. **PAT lifetime vs. session lifetime.** Human PATs max out at 365 days
   (`auth_constants.py:177`). qiita_explore's proxy-session design (Option A)
   should re-verify the PAT periodically via `whoami` and force re-login on
   expiry/disabled/retired. Qiita rejects token use when `disabled` OR
   `retired` is true (`auth/principal.py:224,284-285`).
3. **Profile completion gate.** `POST /auth/pat` requires a complete profile
   (affiliation, address, phone). The handoff path mints the PAT immediately
   at ceiling scope *without* the profile gate — so first-login users get a
   working PAT, but qiita_explore may want to prompt for profile completion
   before granting write scopes. The `profile_complete` flag on
   `WhoAmIHumanResponse` surfaces this.
4. **AuthRocket realm setup is a runbook prerequisite.** Before any of this
   works, the AuthRocket realm must be configured per
   `docs/runbooks/authrocket-realm-setup.md`. This is out-of-band operator
   work, not code.
5. **Email collision on first login** returns 409 + audit event
   (`OIDC_CREATE_PRINCIPAL_EMAIL_CONFLICT`). qiita_explore's login UX should
   handle this gracefully rather than showing a raw 409.
6. **Two `Qiita`s, never conflate.** qiita_explore today reads the *classic*
   Qiita monolith over direct Postgres (`qiita_db.sql_connection.TRN`). The
   new platform (the integration target) is a separate Python/FastAPI control
   plane + Rust/Arrow-Flight data plane at
   `/Users/dhruvsharma/Downloads/Projects/Qiita`. These are not the same
   system. Integrating the new platform's auth means the new platform must be
   deployed and reachable — which intersects with the broader "replace
   classic-Qiita direct-SQL reads with new-platform REST/Flight calls"
   migration (TKT-007).

---

## Source references (all paths under `/Users/dhruvsharma/Downloads/Projects/Qiita`)

| File | Purpose |
|------|---------|
| `qiita-control-plane/src/qiita_control_plane/routes/auth.py` | `/auth/login`, `/auth/handoff`, `/auth/whoami`, `/auth/pat`, `/auth/token`, `/auth/cli-exchange` |
| `qiita-control-plane/src/qiita_control_plane/routes/admin.py:81` | `POST /admin/service-account` — SA provisioning |
| `qiita-control-plane/src/qiita_control_plane/auth/principal.py:158` | `get_current_principal` — bearer-token resolution |
| `qiita-control-plane/src/qiita_control_plane/auth/principal.py:292` | `resolve_oidc` — first-login principal upsert |
| `qiita-control-plane/src/qiita_control_plane/auth/oidc.py` | `JwtVerifier` — RS256, JWKS, claim checks |
| `qiita-control-plane/src/qiita_control_plane/auth/token.py` | `mint_api_token`, `verify_api_token` — `qk_` opaque tokens, SHA-256 hashed |
| `qiita-control-plane/src/qiita_control_plane/auth/scopes.py:101` | `SERVICE_ACCOUNT_SCOPE_CEILING` — flat SA scope set |
| `qiita-control-plane/src/qiita_control_plane/auth/constants.py` | `PAT_MAX_TTL_DAYS=365`, `SERVICE_TOKEN_MAX_TTL_DAYS=3650` |
| `qiita-common/src/qiita_common/api_paths.py` | All route/URL path constants (single source of truth) |
| `qiita-common/src/qiita_common/models.py:1361` | `ServiceAccountCreateResponse`; `:1380` `WhoAmIHumanResponse` |
| `qiita-common/src/qiita_common/client.py` | `ControlPlaneClient` — reference httpx client (async) |
| `qiita-common/src/qiita_common/auth_constants.py` | `SystemRole` enum, `Scope` enum, TTL limits |
