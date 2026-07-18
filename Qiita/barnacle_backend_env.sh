#!/usr/bin/env bash
# ============================================================================
# barnacle_backend_env.sh — run this ON BARNACLE (or the node running the
# QiitaExplore backend) to pin the auth addressing in the backend's .env,
# then restart the backend so it picks the values up.
#
# IMPORTANT: the backend loads config from a FILE via python-dotenv
# (config.py: load_dotenv()), NOT from the shell environment — and it runs as a
# systemd service, which does not inherit your shell env either. So the fix must
# live in the .env file, which is what this script edits (idempotently).
#
# Auth is hosted Qiita-MIINT (https://qiita-miint.ucsd.edu), not the local
# control plane — both URLs point at the same public MIINT origin, since the
# whoami check and the browser-facing login both go straight there now.
#
#   Usage (on barnacle):  bash barnacle_backend_env.sh
# ============================================================================
set -euo pipefail

ENV_FILE="${QIITA_EXPLORE_ENV:-$HOME/qiita-web/qiita_explore/.env}"

# backend -> Qiita-MIINT, for PAT validation (whoami).
CONTROL_PLANE_URL="https://qiita-miint.ucsd.edu"
# browser -> Qiita-MIINT, for the "Log in with Qiita" link.
PUBLIC_LOGIN_URL="https://qiita-miint.ucsd.edu"

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
echo "Verify (from barnacle, no tunnel needed — MIINT is public):"
echo "  curl -s -o /dev/null -w '%{http_code}\\n' https://qiita-miint.ucsd.edu/api/v1/auth/whoami   # expect reachable, non-error"
