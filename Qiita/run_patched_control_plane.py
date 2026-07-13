"""Run the Qiita control plane WITH the AuthRocket "logout-first" login fix,
applied at runtime — without modifying the Qiita repo (it stays at master).

Why runtime-patch instead of editing Qiita:
  The fix wraps the LoginRocket login URL in a LoginRocket `/logout` so a cached
  AuthRocket session can't hijack the login / "Need a login" (signup) entry into
  completing login as the previously-cached user. The logout's redirect_uri is a
  SAME-REALM `/login?...&prompt=login` URL (LoginRocket's SPA only forwards logout
  to same-realm targets — verified: it will NOT forward to an external control-
  plane URL, which is why this can't live purely on the QiitaExplore side).
  Because the control plane must (a) set its signed login-state cookie and
  (b) emit that same-realm logout-wrapped URL, the fix belongs in `begin_login`'s
  URL builder. We monkeypatch that one function here so the Qiita source tree is
  never touched.

Run it with the control plane's own venv/python, from an environment that has the
control-plane vars loaded (see run_control_plane.sh, which sources
Qiita/.env.control-plane first). Port defaults to 8080; override with QCP_PORT.
"""

import os
from urllib.parse import quote


def _build_authrocket_login_url(*, loginrocket_base_url: str, redirect_uri: str) -> str:
    base = loginrocket_base_url.rstrip("/")
    login_url = f"{base}/login?redirect_uri={quote(redirect_uri, safe='')}&prompt=login"
    return f"{base}/logout?redirect_uri={quote(login_url, safe='')}"


def main() -> None:
    # Import main first so routes.auth binds the original symbol, then rebind the
    # name in the module that actually calls it (begin_login resolves it as a
    # module global at call time, so this takes effect for every request).
    import qiita_control_plane.main as cp_main  # noqa: F401  (imports routes.auth)
    import qiita_control_plane.routes.auth as cp_auth

    cp_auth.build_authrocket_login_url = _build_authrocket_login_url

    import uvicorn

    port = int(os.environ.get("QCP_PORT", "8080"))
    uvicorn.run(cp_main.app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
