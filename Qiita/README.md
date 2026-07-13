# AuthRocket "logout-first" login fix (for the Qiita control plane)

This directory holds a **preserved copy** of a fix to the **Qiita control plane**
(`/Users/dhruvsharma/Downloads/Projects/Qiita`). It lives here in qiita-web
because the Qiita repo is kept pristine / in sync with its `master` branch — the
fix is **not** committed there. Reapply it from here when deploying.

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
