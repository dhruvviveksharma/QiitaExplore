# DEPLOYING.md

Everything from the 2026-08-17 kl-remote deployment incident: the topology,
the exact runbook, and every bug that actually bit us. Written for a future
agent or person with zero context on what happened — if you're restarting
this deployment or debugging why it's broken again, start here.

`docs/09-operations.md` documents two deployment topologies for this app:
production (nginx + gunicorn, both on barnacle) and dev (SSH-tunneled from a
developer's own Mac). **This is a third topology** — a public-facing static
host on a machine that is neither barnacle nor the visitor's browser — and
it isn't documented there. That gap is *why* this broke: the two documented
topologies both happen to avoid needing a reverse proxy at all (one machine
does everything, or the tunnel and the browser are the same machine), so
nothing about this deployment shape had been written down anywhere before.

---

## Topology: kl-remote → SSH tunnel → barnacle

```
visitor's browser
      │  https://qiita-explore.knight-lab-dev.org/
      ▼
Cloudflare (inferred from cdn-cgi/rum beacon calls seen in the browser
            network log — never directly confirmed; see "Open questions"
            below)
      │  forwards to kl-remote, almost certainly on :8081
      ▼
kl-remote:8081  —  Caddy
      ├─ /api/*  → reverse_proxy 127.0.0.1:5001  ──┐
      └─ /*      → static files from                │
                   ~/QittaExplore/qiita_explore/     │
                   frontend/ (file_server)           │
                                                      ▼
                                     SSH tunnel (kl-user's own long-running
                                     session): `ssh -N -L 127.0.0.1:5001:
                                     localhost:5001 d4sharma@barnacle2.ucsd.edu`
                                                      │
                                                      ▼
                                     barnacle2.ucsd.edu:5001 — gunicorn,
                                     started via qiita_explore/start_barnacle.sh
                                     (bound to 127.0.0.1 since 2026-10-12)
```

**Why a reverse proxy is required here, structurally**: in both documented
topologies, one machine plays two roles at once (barnacle runs nginx *and*
gunicorn; a developer's own Mac runs the static server *and* is where the
SSH tunnel's local port is real). Here, kl-remote is a third, distinct
machine — not barnacle, not the visitor's computer. The SSH tunnel only
makes `127.0.0.1:5001` real *on kl-remote*; a visitor's browser hitting the
public domain has no way to reach that port directly. Something on
kl-remote has to bridge "public request" → "the tunnel." That's Caddy's job
here, and nothing else in this app's stack does it for you.

This also depends on the frontend defaulting its API base to a **relative**
`/api` path (`qiita_explore/frontend/js/utils.js`, fixed on master via
commits `9542a606`/`e82931dd`) rather than an absolute
`http://127.0.0.1:PORT/api`. The old absolute-URL default only worked when
the browser and the tunnel were the same machine (the dev topology) — it
would silently target the *visitor's own* loopback here, not kl-remote's.
If a future pull ever regresses that default, this whole approach breaks
again, but with a different symptom (see "Known bugs" below).

---

## Components (as of 2026-08-17)

| Component | Location | Notes |
|---|---|---|
| App checkout | `/home/kl-user/QittaExplore` | Note the typo — "QittaExplore," not "QiitaExplore." Real directory name, easy to mis-transcribe. Branch: `master` (user-reported; exact commit not verified from this session). |
| SSH tunnel | run manually in a kl-remote shell | `ssh -N -L 127.0.0.1:5001:localhost:5001 d4sharma@barnacle2.ucsd.edu` — must stay running; nothing supervises it. |
| Caddy binary | `~/caddy` (i.e. `/home/kl-user/caddy`) | v2.11.4, linux/amd64. **Not** the same as `~/QittaExplore/caddy.tar.gz` — see bug #3 below. |
| Caddy config | `~/Caddyfile` | Content below. Proxies `/api/*` to the tunnel, serves everything else as static files. |
| Caddy process | started via `nohup ~/caddy run --config ~/Caddyfile > /tmp/caddy.log 2>&1 &`, run from `~` | Not a systemd service — see "Open follow-ups." |
| Public domain | `qiita-explore.knight-lab-dev.org` | Fronted by something (Cloudflare, inferred) that forwards to kl-remote, almost certainly port 8081. |

**Current `~/Caddyfile`** (confirmed working 2026-08-17):
```caddyfile
:8081 {
	handle /api/* {
		reverse_proxy 127.0.0.1:5001
	}
	handle {
		root * /home/kl-user/QittaExplore/qiita_explore/frontend
		file_server
		try_files {path} /index.html
	}
}
```
Deliberately left as-is rather than "improved" — it's a proven config, not
a draft. If chat responses ever arrive all at once instead of streaming
token-by-token, that's the one thing worth adding
(`flush_interval -1` inside the `reverse_proxy` block disables response
buffering) — but only reach for it if that specific symptom shows up.

---

## Runbook

**Bring it up from scratch** (tunnel + Caddy both down):
```bash
# 1. Start the tunnel (separate terminal/session — leave it running)
ssh -N -L 127.0.0.1:5001:localhost:5001 d4sharma@barnacle2.ucsd.edu

# 2. Start Caddy
cd ~
nohup ./caddy run --config ~/Caddyfile > /tmp/caddy.log 2>&1 &
```

**Restart just Caddy** (tunnel already running):
```bash
pkill -f "caddy run"
cd ~
nohup ./caddy run --config ~/Caddyfile > /tmp/caddy.log 2>&1 &
```

**If port 8081 is occupied by something else** (e.g. a stray
`http.server` — see bug #1): `pkill -f "http.server 8081"` first.

## Verification

```bash
curl -s http://localhost:8081/api/auth/me   # expect: {"anonymous":true,"qiita_url":"https://qiita.ucsd.edu"}
```
(`/api/auth/me` is public and answers without a session. `/api/auth/login-url`,
used here before 2026-10-12, no longer exists.) Then load
`https://qiita-explore.knight-lab-dev.org/` in a real browser: the sign-in
page should render directly at `/` (not a directory listing), with an email +
password form and "Forgot password?" / "Create a Qiita account" links to
qiita.ucsd.edu. Log in with a real classic-Qiita account and send one chat
message to confirm the tunnel→gunicorn path is healthy end-to-end, not just
reachable, and that streaming still works.

---

## Classic-Qiita login: deploy prerequisites and steps (2026-10-12)

The login changed from "paste a qiita-miint `qk_` token" to **classic-Qiita
email + password**, checked against `qiita.qiita_user` in classic Qiita's
production Postgres through QiitaExplore's read-only role. Real Qiita
passwords now cross this deployment, so the path in front of gunicorn
matters in a way it didn't before. Design and threat model:
`docs/02-authentication.md`.

**Stated plainly:** every password is readable in plaintext by Cloudflare
(browser TLS ends there) and by anyone with the `kl-user` account on
kl-remote (Caddy handles the request in plaintext before the SSH tunnel).
That includes admin-level Qiita accounts — every verified level may log in.
The prerequisites below close what can be closed; they don't change that.

### Prerequisites — do these before the first production restart

1. **Encrypt the Cloudflare → kl-remote hop.** Today Caddy listens on plain
   HTTP `:8081`. Either run a **Cloudflare Tunnel** (`cloudflared` on
   kl-remote, outbound only, pointing at Caddy on loopback), or switch
   Cloudflare's SSL/TLS mode to **Full (strict)** with Caddy serving HTTPS
   on that port using a Cloudflare Origin CA certificate. First confirm how
   Cloudflare reaches kl-remote today (see "Cloudflare-fronting is inferred,
   not confirmed" below) — the rest of this list depends on the answer.
2. **Gunicorn now binds `127.0.0.1`** (`start_barnacle.sh`, was `0.0.0.0`).
   The tunnel already targets barnacle's loopback, so nothing else should
   change. Confirm nothing else reaches `barnacle2:5001` directly (another
   proxy, a firewall rule, a second tunnel). Loopback keeps the network out,
   **not** other accounts on barnacle2: any local user can still connect to
   `127.0.0.1:5001` and send any header — including the client-IP header of
   step 3, so the per-client login limit is only as good as barnacle2's user
   isolation (the per-email delay doesn't depend on it). If the kl-remote tunnel reports
   `connect failed` after the restart, change its target from
   `localhost:5001` to `127.0.0.1:5001` (an IPv6 `localhost` won't reach an
   IPv4-only listener).
3. **Caddy: trust the client IP only from Cloudflare, forward it in a header
   Caddy always overwrites, cap request bodies, and forbid framing.** Only
   once this is live, set `QIITA_EXPLORE_CLIENT_IP_HEADER` in the backend
   `.env` (to the header name below); until then leave it unset — a header
   the backend trusts but anyone can send lets an attacker dodge the
   per-client login block or get someone else's IP blocked.
   - `trusted_proxies`: Cloudflare's published ranges
     (https://www.cloudflare.com/ips/) when Cloudflare connects to Caddy
     directly (Full strict); only loopback when `cloudflared` on kl-remote
     is the client (Tunnel) — and then Caddy must listen on loopback only.
   - `client_ip_headers CF-Connecting-IP`, so `{client_ip}` is the
     visitor's address when the request came through a trusted proxy and
     the connection's own address otherwise.
   - Forward it with `header_up X-Real-IP {client_ip}`, which replaces any
     `X-Real-IP` the client sent. Then `QIITA_EXPLORE_CLIENT_IP_HEADER=X-Real-IP`.
   - Cap bodies: the backend already refuses a login body over 8 KB (by
     `Content-Length`, then with a bounded read, so chunked bodies too); a
     Caddy cap stops oversized requests before they reach the tunnel, for
     every route.
   - Send `Content-Security-Policy: frame-ancestors 'none'` (and/or
     `X-Frame-Options: DENY`) on the frontend. The page's own CSP is a
     `<meta>` tag, where browsers ignore `frame-ancestors`.

   A sketch of the result — **untested on kl-remote; adapt to step 1's
   answer and run `./caddy validate --config ~/Caddyfile` before
   restarting Caddy**:
   ```caddyfile
   {
   	servers {
   		trusted_proxies static <Cloudflare ranges — or 127.0.0.1/32 ::1/128 behind cloudflared>
   		client_ip_headers CF-Connecting-IP
   	}
   }

   :8081 {
   	handle /api/auth/login {
   		request_body {
   			max_size 16KB
   		}
   		reverse_proxy 127.0.0.1:5001 {
   			header_up X-Real-IP {client_ip}
   		}
   	}
   	handle /api/* {
   		request_body {
   			max_size 10MB
   		}
   		reverse_proxy 127.0.0.1:5001 {
   			header_up X-Real-IP {client_ip}
   		}
   	}
   	handle {
   		header Content-Security-Policy "frame-ancestors 'none'"
   		header X-Frame-Options DENY
   		root * /home/kl-user/QittaExplore/qiita_explore/frontend
   		file_server
   		try_files {path} /index.html
   	}
   }
   ```
   The `10MB` general cap is a placeholder — check the largest request the
   app legitimately sends before tightening it.
4. **A Cloudflare rate-limiting rule on `POST /api/auth/login`** (Security →
   WAF → Rate limiting rules), per client IP. Cloudflare sees the real
   client IP, which the backend can't by itself; the backend's own limits
   are a per-email progressive delay and, with step 3, a per-client block.
5. **Confirm no shared or demo Qiita account has a known password** in
   production (e.g. a guest or wet-lab admin account). Every verified level
   can now log in to QiitaExplore.

### Production `.env` (barnacle, `/home/d4sharma/QittaExplore/qiita_explore/.env`)

- **Set** `QIITA_EXPLORE_PUBLIC_ORIGIN=https://qiita-explore.knight-lab-dev.org`.
  Login compares the browser's `Origin` to it exactly; without it every
  login answers 503 "Login is not configured on this server."
- **Set** `QIITA_EXPLORE_LIMITER_DB` to a file on barnacle's **local** disk,
  not `/ddn_scratch` (check the filesystem with `df -T <dir>` — `/home` may
  be network-mounted too), in a folder owned by `d4sharma` and not group- or
  world-writable: the limiter refuses anything else and every login then
  answers 503. (Unset, it uses `/tmp/qiita_explore-<uid>/login_limiter.db`,
  which it creates `0700`.) Losing the file is harmless: counters reset and
  browsers lose their known-device exemption.
- **Optional:** `QIITA_CLASSIC_URL` (default `https://qiita.ucsd.edu`; only
  used for the form's "Forgot password?" / "Create a Qiita account" links).
- **Leave** `QIITA_EXPLORE_COOKIE_SECURE` unset (true) — the cookies become
  `__Host-qe_sid` / `__Host-qe_dev` — and `QIITA_EXPLORE_ALLOWED_ORIGINS`
  empty: production is same-origin behind Caddy, and anything listed there
  is also accepted as a login origin.
- **Leave** `QIITA_EXPLORE_CLIENT_IP_HEADER` unset until prerequisite 3 is live.
- **Remove** the qiita-miint variables (now ignored): `QIITA_CONTROL_PLANE_URL`,
  `QIITA_PUBLIC_LOGIN_URL`, `QIITA_LOGINROCKET_URL`,
  `QIITA_WHOAMI_TIMEOUT_SECONDS`, `QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX`,
  `QIITA_EXPLORE_DEBUG_ERRORS`, and any old `QIITA_EXPLORE_PAT_ENCRYPTION_KEY`.

### Steps

Do steps 3–5 in one sitting: between the restart and the binding, an old
user's first login would create a new, empty account (see step 5).

1. **Back up the database** on barnacle:
   ```bash
   sqlite3 /ddn_scratch/d4sharma/QiitaExploreDB/deployment/projects.db \
     ".backup '/ddn_scratch/d4sharma/QiitaExploreDB/deployment/projects.db.pre-classic-login-$(date +%Y%m%d%H%M%S)'"
   ```
2. **Check `bcrypt` is in the `qiita-web` conda env** (shared by dev and
   prod): `conda run -n qiita-web python -c "import bcrypt; print(bcrypt.__version__)"`
   should print a 4.x version. If not, `pip install 'bcrypt==4.3.0'` in that
   env. Without it the backend can't import and won't boot.
3. **Pull and restart production (5001) — only 5001.** In
   `/home/d4sharma/QittaExplore`, pull `master`. Stop the running gunicorn
   **by PID**: `ss -ltnp | grep ':5001 '` shows the master process; `kill`
   that PID. Never `pkill -f gunicorn` (it also kills dev on 5002), and never
   put a `pkill -f "gunicorn…5001"` inside an `ssh barnacle2 '…'` command
   string — the pattern matches that remote shell's own command line and
   the shell kills itself. The old process listens on `0.0.0.0:5001`; the
   new one will be on `127.0.0.1:5001`. Start it again:
   ```bash
   cd /home/d4sharma/QittaExplore
   nohup bash qiita_explore/start_barnacle.sh > /tmp/qe-5001.log 2>&1 &
   ```
   `start_barnacle.sh` imports `store.db` once before gunicorn starts, so
   the `users` migration runs there, once: every existing account is kept
   (its qiita-miint email moves to `miint_email`, `qiita_email` stays
   `NULL`) and **every existing session is revoked**. The dry run on a copy
   of the production DB (2026-10-12) kept 4 users and revoked 11 sessions,
   with an empty foreign-key check and a clean integrity check. **This
   restart also applies earlier pending migrations:** the production
   gunicorn has run since 2026-09-03 and was never restarted after `master`
   was pulled to `43b9a5c`, so this boot also creates the Sample Aggregation
   tables, `chat_exports` and `study_sample_files_cache`, and adds
   `global_chats.context_usage` and `study_detail_cache.sample_files_json`.
   Check `/tmp/qe-5001.log`, then on barnacle:
   `curl -s http://127.0.0.1:5001/api/auth/me` → `{"anonymous":true,...}`.
4. **Pull the frontend on kl-remote at the same time**
   (`/home/kl-user/QittaExplore`). The old page calls
   `/api/auth/connect` and `/api/auth/login-url`, which now 404. The new
   `index.html` bumps every changed script's `?v=`; if the old sign-in page
   still shows, purge `index.html` from Cloudflare's cache. The page's CSP
   blocks scripts from hosts other than unpkg and jsdelivr, so if Cloudflare
   injects its analytics beacon, expect a harmless CSP error for it in the
   browser console.
5. **Bind the old accounts — dry run first.** The script needs the same
   environment `start_barnacle.sh` sets up; `.env` alone is not enough.
   Without `QIITA_EXPERIMENT_DB_PATH` it refuses to run (it would otherwise
   open `backend/data/projects.db`, a different database); it prints the
   database path it uses first — check it.
   ```bash
   cd /home/d4sharma/QittaExplore/qiita_explore/backend
   conda activate qiita-web
   export QIITA_CONFIG_FP=/home/d4sharma/qiita-web/qiita_config.cfg
   export QIITA_EXPERIMENT_DB_PATH=/ddn_scratch/d4sharma/QiitaExploreDB/deployment/projects.db
   python ../scripts/bind_classic_accounts.py          # dry run: lists, changes nothing
   ```
   Review each `user_id → email` mapping **with Dhruv** — qiita-miint never
   proved its users owned their emails, so a match on email alone is not
   proof. Then bind only the confirmed ones:
   ```bash
   python ../scripts/bind_classic_accounts.py --apply <user_id>=<classic email> ...
   ```
   It binds a pair only if a **verified** classic account exists for that
   email, the account is still unbound, and the email isn't bound
   elsewhere. Do this **before those people first log in**: a first login
   creates a new, empty account bound to their email, after which the
   script can't bind the old one to it.
6. **Check for an old Anthropic key in the database.** `POST /api/settings`
   is gone, but a key stored through it earlier is still preferred over
   `.env`'s `ANTHROPIC_API_KEY`:
   ```bash
   sqlite3 /ddn_scratch/d4sharma/QiitaExploreDB/deployment/projects.db \
     "SELECT key FROM meta WHERE key = 'anthropic_api_key';"
   ```
   If a row comes back and it isn't the key you intend to bill, delete it
   (`DELETE FROM meta WHERE key = 'anthropic_api_key';`) so the `.env` key
   applies. Any user could have written it.
7. **Verify** as in "Verification" above, with a real classic-Qiita login.

---

## Known bugs hit during the 2026-08-17 incident

**1. `python3 -m http.server` cannot reverse-proxy — architecturally, not
as a config mistake.** It was used as a quick stand-in for the frontend
server. It's a pure static file server with no proxy capability at all, so
`/api/*` requests 404 through it regardless of which directory it's run
from. Symptom was subtle: the page loaded, but "Log in with Qiita" and
"Connect" sat visually disabled because the JS calls that populate them
(`/api/auth/me`, `/api/auth/login-url`) both failed. (Those were the old
qiita-miint login's controls. Since 2026-10-12 the page shows an email +
password form instead, and the same failure looks like the form rendering
but every attempt failing with "Sign-in failed (<status>)", because
`/api/auth/me` and `POST /api/auth/login` never reach gunicorn.) **Never use a plain
static file server for this topology** — it needs something with reverse
proxy capability (Caddy here; nginx would also work).

**2. Serving from the wrong working directory.** Before diagnosing bug #1,
the more visible symptom was `python3 -m http.server 8081` run from the
repo root showing a raw directory listing at `/` — there's no `index.html`
there, only inside `qiita_explore/frontend/`. Fixing the directory alone
would *not* have fixed bug #1; both had to be addressed.

**3. Two different `caddy.tar.gz` files, one a red herring.**
`~/QittaExplore/caddy.tar.gz` (inside the git checkout) turned out to
contain the literal text `"Not Found"` — a failed-download artifact from
some earlier bad URL, unrelated to git tracking (it isn't part of this
repo's tracked files) and unrelated to the actual working Caddy install.
The real one is `~/caddy.tar.gz` (home directory), a legitimate GitHub
release archive that was already extracted to `~/caddy`. Time was nearly
spent re-downloading Caddy from scratch before checking whether a working
copy already existed. **If `caddy.tar.gz` misbehaves, check `file
<path>` before assuming it needs re-fetching — and check `~` for an
existing `caddy` binary/`Caddyfile` first.**

**4. The actual root cause: a silently-dead background process, replaced
by a non-functional stopgap.** Shell history shows a fully-working,
`nohup`-backgrounded Caddy process from an earlier session (tested against
the exact `/api/auth/me` endpoint that later failed). At some point it
stopped running — cause not determined from history alone (reboot, crash,
and manual kill are all consistent with the evidence available). The
recovery action was to start `python3 -m http.server 8081` on the same
port as a fast stopgap, which served *something* but couldn't proxy
`/api/*` — reintroducing bug #1 without anyone realizing Caddy had been
doing more than static file serving. **If this deployment breaks again,
check whether Caddy is actually running (`ps aux | grep caddy`) before
reaching for any other fix.**

**5. `nohup ... &` does not survive a kl-remote reboot.** Currently the
only thing keeping Caddy running is a backgrounded shell process with no
supervisor. This is almost certainly what caused bug #4's silent death in
the first place. See "Open follow-ups."

---

## Open follow-ups / risks (not fixed, flagging for whoever picks this up)

- **No process supervision.** Caddy (and the SSH tunnel) run as bare
  `nohup`/foreground processes. A reboot, an OOM kill, or a crash takes the
  whole public deployment down with no automatic recovery and no alert.
  Worth a systemd service (or at minimum a screen/tmux session) for both
  the tunnel and Caddy if this needs to stay up unattended.
- **Cloudflare-fronting is inferred, not confirmed.** The `cdn-cgi/rum`
  beacon calls seen in the browser's network log strongly suggest
  Cloudflare sits in front of `qiita-explore.knight-lab-dev.org`, and that
  it forwards to kl-remote on port 8081 specifically (matching the port
  already in use) — but no `cloudflared` config or equivalent was actually
  located or inspected during this session. Whoever has access to that
  layer should confirm where it's configured and document it here.
- **This whole approach depends on the frontend's relative-`/api` default
  staying in place.** If a future change to `qiita_explore/frontend/js/utils.js`
  reintroduces a hardcoded absolute API base (as some other branches had
  during 2026-08 development), this deployment will break again — but with
  a different symptom (the browser attempting to reach `127.0.0.1:PORT`
  directly, i.e. the *visitor's own* machine, showing as a connection
  failure in browser dev tools rather than a same-origin 404 in Caddy).
  Worth checking `utils.js`'s API-base resolution first if login breaks
  again with network errors instead of 404s.
