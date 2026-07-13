#!/usr/bin/env bash
# Run the Qiita control plane (auth server) on your Mac WITH the AuthRocket
# "logout-first" fix applied at runtime — the Qiita repo stays at master.
#
# The fix (see run_patched_control_plane.py) makes /api/v1/auth/login redirect
# through LoginRocket /logout first, so a cached AuthRocket session can't hijack
# the login / "Need a login" (signup) entry into the previously-cached account.
#
# Usage (on the Mac):
#   1. Stop any control plane already on :8080 (Ctrl+C in its terminal).
#   2. bash /Users/dhruvsharma/Downloads/Projects/qiita-web/Qiita/run_control_plane.sh
#   3. Verify:  curl -sI http://127.0.0.1:8080/api/v1/auth/login | grep -i location
#      -> Location must contain  loginrocket.com/logout?redirect_uri=
set -euo pipefail

QIITA_DIR="/Users/dhruvsharma/Downloads/Projects/Qiita"
LAUNCHER="/Users/dhruvsharma/Downloads/Projects/qiita-web/Qiita/run_patched_control_plane.py"

# Load the control-plane secrets/config (FLIGHT_TICKET_SIGNING_KEY,
# LOGIN_COOKIE_SECRET_KEY, DATABASE_URL, AUTHROCKET_*, PATH_SCRATCH, ...).
set -a
. "${QIITA_DIR}/.env.control-plane"
set +a

export QCP_PORT="${QCP_PORT:-8080}"

exec "${QIITA_DIR}/qiita-control-plane/.venv/bin/python" "${LAUNCHER}"
