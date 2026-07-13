#!/usr/bin/env bash
# Environment for the QiitaExplore BACKEND (gunicorn) — source this on barnacle
# (or whatever node runs the backend) BEFORE starting gunicorn:
#
#   source /path/to/barnacle_backend_env.sh
#   bash qiita_explore/start_barnacle.sh          # (or your dev launch command)
#
# ── The connectivity fix (this is what "Qiita is temporarily unreachable" was) ──
# There are TWO distinct addresses for the control plane, on purpose:
#   * the BROWSER runs on your Mac and reaches the control plane directly on :8080
#   * the BACKEND runs here (barnacle) and reaches the control plane through a
#     REVERSE ssh tunnel that maps barnacle:18080 -> Mac:8080.
# The backend's whoami calls use QIITA_CONTROL_PLANE_URL; the browser login link
# uses QIITA_PUBLIC_LOGIN_URL. They must NOT be the same value in this split setup.

export QIITA_CONTROL_PLANE_URL="http://127.0.0.1:18080"   # backend  -> control plane (via reverse tunnel)
export QIITA_PUBLIC_LOGIN_URL="http://127.0.0.1:8080"     # browser  -> control plane (direct, on the Mac)

# Frontend is served on your Mac at :5503, so allow that credentialed origin.
export QIITA_EXPLORE_ALLOWED_ORIGINS="http://127.0.0.1:5503"
export QIITA_EXPLORE_COOKIE_SECURE="false"                # plain-HTTP local dev

# Do NOT set QIITA_LOGINROCKET_URL. The QiitaExplore-side logout-wrap does not
# work: LoginRocket's SPA refuses to forward /logout to an external control-plane
# URL (verified), so the "logout-first" fix lives in the control plane instead
# (run via Qiita/run_control_plane.sh on the Mac). Leaving it unset keeps the
# login URL pointing straight at the control plane, which is correct.
unset QIITA_LOGINROCKET_URL

# KEEP your existing PAT encryption key — regenerating it invalidates every
# stored session (users would have to reconnect their token). Set it to whatever
# the backend already uses; do not generate a new one here:
#   export QIITA_EXPLORE_PAT_ENCRYPTION_KEY="<your existing Fernet key>"

# ── The reverse tunnel must be running (start it FROM your Mac, once) ──
#   ssh -f -N -R 18080:localhost:8080 d4sharma@barnacle2.ucsd.edu
# Verify from barnacle that the control plane is reachable through it:
#   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/api/v1/auth/whoami   # expect 401 (reachable)

echo "QiitaExplore backend env loaded:"
echo "  QIITA_CONTROL_PLANE_URL = ${QIITA_CONTROL_PLANE_URL}  (backend -> control plane, needs reverse tunnel)"
echo "  QIITA_PUBLIC_LOGIN_URL  = ${QIITA_PUBLIC_LOGIN_URL}  (browser login link)"
echo "  QIITA_LOGINROCKET_URL   = (unset — intentionally)"
