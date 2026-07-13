#!/usr/bin/env bash
# ============================================================================
# barnacle_backend_env.sh — run this ON BARNACLE (or the node running the
# QiitaExplore backend) to pin the control-plane addressing in the backend's
# .env, then restart the backend so it picks the values up.
#
# IMPORTANT: the backend loads config from a FILE via python-dotenv
# (config.py: load_dotenv()), NOT from the shell environment — and it runs as a
# systemd service, which does not inherit your shell env either. So the fix must
# live in the .env file, which is what this script edits (idempotently).
#
#   Usage (on barnacle):  bash barnacle_backend_env.sh
# ============================================================================
set -euo pipefail

ENV_FILE="${QIITA_EXPLORE_ENV:-$HOME/qiita-web/qiita_explore/.env}"

# backend -> control plane, via the REVERSE ssh tunnel opened from the Mac
# (barnacle:18080 -> Mac:8080). This is the value whose absence caused
# "Qiita is temporarily unreachable".
CONTROL_PLANE_URL="http://127.0.0.1:18080"
# browser -> control plane (the browser runs on the Mac and hits Mac:8080).
PUBLIC_LOGIN_URL="http://127.0.0.1:8080"

if [ ! -f "${ENV_FILE}" ]; then
  echo "ERROR: ${ENV_FILE} not found. Set QIITA_EXPLORE_ENV to the backend's .env path." >&2
  exit 1
fi

cp "${ENV_FILE}" "${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"

set_kv() {  # set_kv KEY VALUE  — replace existing (with or without spaces around =) or append
  local key="$1" val="$2"
  # drop any existing line for this key (tolerates "KEY=" and "KEY = ")
  sed -i.tmp "/^[[:space:]]*${key}[[:space:]]*=/d" "${ENV_FILE}" && rm -f "${ENV_FILE}.tmp"
  printf '%s=%s\n' "${key}" "${val}" >> "${ENV_FILE}"
}

set_kv QIITA_CONTROL_PLANE_URL "${CONTROL_PLANE_URL}"
set_kv QIITA_PUBLIC_LOGIN_URL  "${PUBLIC_LOGIN_URL}"
# The QiitaExplore-side logout-wrap does NOT work (LoginRocket won't forward
# /logout to an external URL), so make sure it is not set:
sed -i.tmp "/^[[:space:]]*QIITA_LOGINROCKET_URL[[:space:]]*=/d" "${ENV_FILE}" && rm -f "${ENV_FILE}.tmp"

echo "Pinned in ${ENV_FILE}:"
grep -E "QIITA_CONTROL_PLANE_URL|QIITA_PUBLIC_LOGIN_URL" "${ENV_FILE}" | sed 's/^/  /'

echo
echo "Now RESTART the backend so it reloads the .env, e.g.:"
echo "  systemctl --user restart <your-qiita-backend.service>   # if a user service"
echo "  sudo systemctl restart <your-qiita-backend.service>     # if a system service"
echo "  # or, if you launch it manually:  pkill -f gunicorn ; bash qiita_explore/start_barnacle.sh"
echo
echo "Verify (from barnacle, needs the reverse tunnel up on the Mac):"
echo "  curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:18080/api/v1/auth/whoami   # expect 401 (reachable)"
