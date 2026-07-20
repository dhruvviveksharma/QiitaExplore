---
name: oidc-pat-flow-gotchas
description: Non-obvious details of the new Qiita platform's AuthRocket login/PAT-mint flow relevant to any client (like qiita-web) building its own session layer on top
metadata:
  type: reference
---

From `/Users/dhruvsharma/Downloads/Projects/Qiita/docs/auth.md` and
`qiita-control-plane/src/qiita_control_plane/routes/auth.py`.

**Re-verified 2026-07-12 against commit `837fc6cd`** (routes/auth.py itself was rewritten
in commit `9f955d2f`, 2026-07-09 — substantially changed since the 2026-07-06 snapshot this
memory originally recorded). Re-verify again before relying on line numbers if this is read
more than ~1 month after 2026-07-12; the module docstring at `routes/auth.py:1-21` is the
fastest way to confirm the shape hasn't shifted again.

## Current flow shape (as of 837fc6cd)

Three routes work together, discriminated by a signed, HttpOnly login cookie
(`LOGIN_COOKIE_NAME`, set by `/auth/login`, verified by `/auth/handoff`):

- `GET /auth/login` (`routes/auth.py:380-438`) — sets the cookie (payload: timestamp +
  `cli` bool + optional loopback `port`), 302s to AuthRocket's hosted LoginRocket Web UI
  with `prompt=login`. Accepts `?cli=1&port=N` to branch into the CLI loopback flow.
- `GET /auth/handoff?token=<JWT>` (`routes/auth.py:488-670`) — verifies the cookie's
  freshness + the JWT, runs the OIDC resolver upsert, then picks ONE of three sub-flows by
  cookie contents:
  - **CLI flow** (cookie has `cli=true`): mints the PAT, stores it server-side keyed by a
    one-time `ot_code`, 302-redirects the browser to
    `http://127.0.0.1:<port>/?ot_code=<code>` (the CLI's own loopback listener), cookie
    scrubbed. **No PAT in this redirect** — only the ot_code.
  - **Browser-login flow** (cookie present, no `cli`): renders `_HANDOFF_BROWSER_HTML`
    (`routes/auth.py:446-472`) — an HTML page with the PAT plaintext in a `<pre>` for
    copy-paste. **Still no JSON success response for this path** — original finding holds.
  - **Invitation flow** (cookie absent — AuthRocket sent the user straight here without
    transiting `/auth/login`): mints NOTHING; just upserts the principal via the OIDC
    resolver and 302-redirects to `/auth/login` so the PAT only ever gets minted through a
    cookie-anchored path. (This is new/changed from the prior snapshot — previously the
    invitation flow rendered the same HTML directly.)
- `POST /auth/cli-exchange` (`routes/auth.py:692-749`) — redeems the `ot_code` from the CLI
  flow above, returns `ApiTokenMintResponse` (JSON: `token`, `token_idx`, `label`, `scopes`,
  `expires_at`, `created_at`). Atomic single-use consume; 404 conflates
  not-found/expired/already-used.

**Actionable for qiita-web:** the CLI loopback dance (`cli=1&port=N` → catch the
`ot_code` redirect on a local listener → `POST /auth/cli-exchange`) is the ONLY
documented way to get the PAT as JSON without HTML-scraping. If qiita-web's backend wants
a "Login with Qiita" button that doesn't scrape HTML, mimic the CLI flow exactly (bind an
ephemeral loopback port, open `/auth/login?cli=1&port=<port>` in the user's browser, run a
tiny local HTTP server to catch `?ot_code=...`, then POST to `/auth/cli-exchange`). The
qiita-admin CLI's own implementation is the reference: `qiita_control_plane/src/
qiita_control_plane/cli/_common.py:515` (`do_login`).

- **`POST /auth/pat` is now explicitly documented as legacy** (module docstring
  `routes/auth.py:18-20`: "continues to mint PATs from a bearer JWT for backward
  compatibility with the operator out-of-band path; the /auth/login flow above is the
  supported route forward"). It requires the JWT's `auth_time` claim to be fresh
  (`AUTHROCKET_PAT_MAX_AUTH_AGE_SECONDS`, default 300s) — but the `handoff` docstring
  (`routes/auth.py:531-534`) notes **"The realm emits no `auth_time`"** for the real
  AuthRocket/LoginRocket Web integration. So `POST /auth/pat` is effectively DEAD against
  the real IdP in production — it only works in tests where `JwksHarness`-signed JWTs
  include a synthetic `auth_time` claim. Do not plan qiita-web's integration around
  `POST /auth/pat`; use the login/handoff/cli-exchange trio instead.
- **The PAT is genuinely shown once** — plaintext is never persisted anywhere after the
  mint response / HTML render (`ApiTokenMintResponse.token` docstring,
  `qiita-common/src/qiita_common/models/auth.py:34-44`; DB stores only
  `SHA-256(plaintext)`). No "reveal token" admin escape hatch. (Note: `models.py` in this
  repo is now a package — `qiita_common/models/` with submodules like `auth.py`,
  `study.py` — not a flat file; `from qiita_common.models import X` still works via
  `models/__init__.py` re-exports, so import sites are unaffected, but old line-number
  citations against a flat `models.py` no longer resolve.)
- **OIDC-resolved principals get the FULL role ceiling as their scope set**, not a
  narrowed set — still true; `role_ceiling(role)` is used both for the plain OIDC resolve
  and for handoff's auto-mint. Only PATs minted via `/auth/pat` (with an explicit
  `scopes=[...]` body) can carry a narrower scope list.
- **Stable identifier to key on: `principal_idx`** (bigint PK on `qiita.principal`) — not
  `(iss, sub)` directly, though that pair is what's looked up (`qiita.user_identity`
  table) to resolve it. Safe to use as the SQLite `user_id` join key in qiita-web, but it's
  platform-internal and opaque (an integer), not a UUID.
