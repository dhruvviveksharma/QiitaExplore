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
curl -s http://localhost:8081/api/auth/me          # expect: {"anonymous":true}
curl -s http://localhost:8081/api/auth/login-url   # expect: {"url":"https://qiita-miint.ucsd.edu/..."}
```
Then load `https://qiita-explore.knight-lab-dev.org/` in a real browser:
the login page should render directly at `/` (not a directory listing),
with an active "Log in with Qiita" button. Complete a real login and send
one chat message to confirm the tunnel→gunicorn path is healthy end-to-end,
not just reachable, and that streaming still works.

---

## Known bugs hit during the 2026-08-17 incident

**1. `python3 -m http.server` cannot reverse-proxy — architecturally, not
as a config mistake.** It was used as a quick stand-in for the frontend
server. It's a pure static file server with no proxy capability at all, so
`/api/*` requests 404 through it regardless of which directory it's run
from. Symptom was subtle: the page loaded, but "Log in with Qiita" and
"Connect" sat visually disabled because the JS calls that populate them
(`/api/auth/me`, `/api/auth/login-url`) both failed. **Never use a plain
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
