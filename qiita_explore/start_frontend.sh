#!/usr/bin/env bash
# Laptop-side frontend launcher. Everything the browser needs except the SSH
# tunnel, which stays separate because it must hold its own terminal.
#
#   bash qiita_explore/start_frontend.sh              # backend on 5002 (barnacle via tunnel)
#   API_PORT=5001 bash .../start_frontend.sh          # backend running locally on this Mac
#   FRONTEND_PORT=5503 bash .../start_frontend.sh     # if 3000 is taken by something you want to keep
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
INDEX="$FRONTEND_DIR/index.html"

API_PORT="${API_PORT:-5002}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
API_BASE="http://127.0.0.1:${API_PORT}/api"

# ── 1. The frontend origin must be in the backend's allowlist ───────────────
# Serving from any other port means POST /api/auth/connect returns 403
# "origin not allowed" (routes/auth_routes.py :: _origin_allowed) — a failure
# that shows up only at login and reads like a broken password, so check now.
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  ORIGINS="$(grep -E '^QIITA_EXPLORE_ALLOWED_ORIGINS=' "$ENV_FILE" | cut -d= -f2- || true)"
  if [ -n "$ORIGINS" ] && ! printf '%s' "$ORIGINS" | tr ',' '\n' | grep -qx "http://localhost:${FRONTEND_PORT}"; then
    echo "WARNING: http://localhost:${FRONTEND_PORT} is not in QIITA_EXPLORE_ALLOWED_ORIGINS." >&2
    echo "         Login will 403. Allowed: $ORIGINS" >&2
    echo "         Re-run with FRONTEND_PORT=<one of those>, or add this origin to .env" >&2
    echo "         (note: that .env is read by the BACKEND, so on barnacle when tunnelling)." >&2
  fi
fi

# ── 2. Point index.html at the right backend ────────────────────────────────
# python3 rather than sed -i: no BSD/GNU divergence, and it is a no-op when the
# value already matches so the file does not churn in git on every launch.
python3 - "$INDEX" "$API_BASE" <<'PY'
import re, sys
path, api_base = sys.argv[1], sys.argv[2]
html = open(path).read()
new = re.sub(r'(<meta name="api-base" content=")[^"]*(")', lambda m: m.group(1) + api_base + m.group(2), html, count=1)
if new == html:
    print(f"api-base already {api_base}")
else:
    open(path, "w").write(new)
    print(f"api-base -> {api_base}")
PY

# ── 3. Reuse an existing server on this port rather than fighting it ────────
# `serve -l N` silently falls back to a random port when N is busy, and that
# random port is never in the allowlist. So resolve the conflict here instead.
if lsof -nP -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  if curl -sf "http://127.0.0.1:${FRONTEND_PORT}/" 2>/dev/null | grep -q '<title>Qiita Explorer</title>'; then
    echo "Already serving the Qiita frontend on http://localhost:${FRONTEND_PORT} — reusing it."
    echo "It reads index.html from disk per request, so the api-base change above is already live."
    echo "Hard-refresh the browser (Cmd-Shift-R) to drop the cached copy."
    exit 0
  fi
  echo "Port ${FRONTEND_PORT} is in use by something that is not this frontend:" >&2
  lsof -nP -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN >&2
  echo "Free it, or re-run with FRONTEND_PORT=<other allowlisted port>." >&2
  exit 1
fi

# ── 4. Warn if the backend is unreachable, but do not block ─────────────────
# 401 is the healthy answer here: /api/global-chats is auth-protected, so a 401
# proves the backend is up and enforcing. 000 means nothing answered.
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${API_PORT}/api/global-chats" || echo 000)"
case "$CODE" in
  000) echo "WARNING: nothing answering on 127.0.0.1:${API_PORT} — is the SSH tunnel up?" >&2
       echo "         ssh -N -L 127.0.0.1:${API_PORT}:localhost:${API_PORT} d4sharma@barnacle2.ucsd.edu" >&2 ;;
  401) echo "Backend reachable on :${API_PORT} (401 = up and enforcing auth)." ;;
  *)   echo "Backend on :${API_PORT} answered HTTP ${CODE}." ;;
esac

echo "Serving $FRONTEND_DIR on http://localhost:${FRONTEND_PORT} (api-base ${API_BASE})"
exec npx serve "$FRONTEND_DIR" --no-clipboard -l "$FRONTEND_PORT"
