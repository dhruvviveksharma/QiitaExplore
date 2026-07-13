# QiitaExplore auth — run scripts + AuthRocket notes

## Quick start (bring up the whole stack)

On your **Mac**, one command starts the control plane + both SSH tunnels + the
frontend:

```
bash Qiita/start_qiita_stack.sh
```

Then open `http://127.0.0.1:5503/qiita_explore/frontend/index.html`.

Topology — the two tunnels are the crux (and the down reverse tunnel was the
"Qiita is temporarily unreachable" error):

| Path | How | Address |
|------|-----|---------|
| Control plane (auth server) | runs on the Mac | `127.0.0.1:8080` |
| Browser → backend | **forward** tunnel | `Mac:5002 → barnacle:5002` |
| Backend → control plane (whoami) | **reverse** tunnel | `barnacle:18080 → Mac:8080` |
| Backend (gunicorn) | systemd service on barnacle | `:5002` |
| Frontend | static server on the Mac | `:5503` |

The backend's env lives in `~/qiita-web/qiita_explore/.env` on barnacle (loaded
by `load_dotenv()`, **not** the shell env — the backend is a systemd service).
It must contain:

```
QIITA_CONTROL_PLANE_URL=http://127.0.0.1:18080   # backend → control plane (reverse tunnel)
QIITA_PUBLIC_LOGIN_URL=http://127.0.0.1:8080      # browser → control plane
```

Run `bash Qiita/barnacle_backend_env.sh` **on barnacle** to pin those and get the
restart command. Files in this directory:

- `start_qiita_stack.sh` — Mac-side: start control plane + tunnels + frontend.
- `barnacle_backend_env.sh` — barnacle-side: pin the `.env` URLs + restart hint.
- `run_control_plane.sh` / `run_patched_control_plane.py` — run the control plane
  with the optional logout-first fix (see caveat below).

---

## The AuthRocket "logout-first" fix (optional)

Preserved copy of a fix to the **Qiita control plane**
(`/Users/dhruvsharma/Downloads/Projects/Qiita`), kept here because the Qiita repo
is kept pristine at `master`. **Caveat:** it prevents the "Need a login" hijack
only when you already have an AuthRocket session; a fully logged-OUT first login
can fail to mint a token until a retry (AuthRocket's `/logout` drops the handoff
redirect when there is no session). The QiitaExplore-side variant does **not**
work at all — LoginRocket refuses to forward `/logout` to an external URL. So this
is optional; `start_qiita_stack.sh` runs the plain master control plane by default
(set `PATCHED=1` to use the fix).

## What it fixes

Clicking **"Need a login"** (or otherwise switching accounts) on the hosted
AuthRocket LoginRocket page would hand back whatever account already had a
cached LoginRocket session (e.g. you'd get logged in as `test2@test.com` /
`test4@test.com` instead of a signup form). A cached AuthRocket session
short-circuits any navigation — including `/signup` — into "already signed in →
complete the OAuth redirect", minting a PAT for the wrong user.

`prompt=login` alone doesn't stop this — it only forces the login *form*, not a
session clear. The fix routes the login entry through LoginRocket **`/logout`**
first, so the session is cleared before the user reaches `/login`. LoginRocket's
`/logout` honors `redirect_uri` and its SPA preserves the inner login
`redirect_uri` in `sessionStorage`, so the post-login `/auth/handoff` redirect
still fires (verified live).

## Files (mirrors the paths under the Qiita repo root)

- `qiita-control-plane/src/qiita_control_plane/auth/handoff.py` — `build_authrocket_login_url` now wraps the `/login?…&prompt=login` URL in `/logout?redirect_uri=`.
- `qiita-control-plane/src/qiita_control_plane/routes/auth.py` — `begin_login` docstring note only (no logic change).
- `qiita-control-plane/tests/auth/test_handoff.py` — the two URL-builder tests updated to assert the logout-wrapped shape (18/18 pass).

## How to reapply to the Qiita repo

Option A — git patch (from the Qiita repo root):

```
cd /Users/dhruvsharma/Downloads/Projects/Qiita
git apply /Users/dhruvsharma/Downloads/Projects/qiita-web/Qiita/authrocket-logout-first-fix.patch
```

Option B — copy the mirrored files over the same relative paths under the Qiita
repo root.

Then restart the control plane to load it:

```
cd /Users/dhruvsharma/Downloads/Projects/Qiita/qiita-control-plane
set -a; . ../.env.control-plane; set +a
.venv/bin/uvicorn qiita_control_plane.main:app --host 127.0.0.1 --port 8080
```

Verify:

```
curl -sI http://127.0.0.1:8080/api/v1/auth/login | grep -i location
# → Location: https://<realm>.loginrocket.com/logout?redirect_uri=…%2Flogin%3F…&prompt=login
```

## ⚠️ Note on the currently-running server

The live control plane on `127.0.0.1:8080` already has this patched code loaded
**in memory**. Once the Qiita working tree is reverted, restarting that server
from Qiita source will drop back to the unpatched behavior unless you reapply
this patch first.
