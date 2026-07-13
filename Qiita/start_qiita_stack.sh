#!/usr/bin/env bash
# ============================================================================
# start_qiita_stack.sh — bring up the entire QiitaExplore auth stack, one shot.
# Run this on your MAC:   bash qiita-web/Qiita/start_qiita_stack.sh
#
# Topology (why there are two tunnels):
#   * Control plane (auth server)  -> runs HERE on the Mac at 127.0.0.1:8080
#   * QiitaExplore backend (gunicorn) -> runs on BARNACLE (systemd service),
#     and is reached TWO different ways:
#       - browser  -> backend        via FORWARD tunnel  Mac:5002  -> barnacle:5002
#       - backend  -> control plane  via REVERSE tunnel  barnacle:18080 -> Mac:8080
#   * Frontend -> served on the Mac at :5503 (Cursor live server, or the
#     fallback static server this script can start).
#
# The barnacle backend's env lives in ~/qiita-web/qiita_explore/.env and is
# already pinned to the matching addresses (keep them exactly):
#       QIITA_CONTROL_PLANE_URL = http://127.0.0.1:18080   # backend -> control plane
#       QIITA_PUBLIC_LOGIN_URL  = http://127.0.0.1:8080    # browser -> control plane
#   (see Qiita/barnacle_backend_env.sh to (re)apply them + restart the service).
# ============================================================================
set -uo pipefail

QIITA_DIR="/Users/dhruvsharma/Downloads/Projects/Qiita"
WEB_DIR="/Users/dhruvsharma/Downloads/Projects/qiita-web"
BARNACLE="d4sharma@barnacle2.ucsd.edu"
CP_LOG="/tmp/qiita-control-plane.log"

# Set to 1 to run the anti-hijack "logout-first" control plane instead of master.
# CAVEAT: fixes "Need a login" hijack when you already have an AuthRocket session,
# but a fully logged-OUT first login can fail to mint a token until a retry
# (AuthRocket's /logout drops the handoff redirect when there's no session).
PATCHED="${PATCHED:-0}"

log(){ printf '  %s\n' "$*"; }
up(){ curl -s -o /dev/null -m 2 "$1" 2>/dev/null; }

echo "==> QiitaExplore stack"

# 1) Control plane on :8080 -------------------------------------------------
if up http://127.0.0.1:8080/api/v1/auth/login; then
  log "control plane already up on :8080 — leaving it"
else
  set -a; . "${QIITA_DIR}/.env.control-plane"; set +a
  if [ "${PATCHED}" = "1" ]; then
    log "starting PATCHED control plane (logout-first) on :8080 ..."
    QCP_PORT=8080 nohup "${QIITA_DIR}/qiita-control-plane/.venv/bin/python" \
      "${WEB_DIR}/Qiita/run_patched_control_plane.py" >"${CP_LOG}" 2>&1 &
  else
    log "starting control plane (master) on :8080 ..."
    nohup "${QIITA_DIR}/qiita-control-plane/.venv/bin/uvicorn" \
      qiita_control_plane.main:app --host 127.0.0.1 --port 8080 >"${CP_LOG}" 2>&1 &
  fi
  log "  logs -> ${CP_LOG}"
fi

# 2) Reverse tunnel  barnacle:18080 -> Mac:8080  (backend -> control plane) --
if pgrep -f "ssh.*-R 18080:localhost:8080" >/dev/null; then
  log "reverse tunnel :18080 already up"
else
  log "opening reverse tunnel  barnacle:18080 -> :8080 ..."
  ssh -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -R 18080:localhost:8080 "${BARNACLE}" && log "  ok" || log "  FAILED (check ssh)"
fi

# 3) Forward tunnel  Mac:5002 -> barnacle:5002  (browser -> backend) --------
if pgrep -f "ssh.*-L 5002:localhost:5002" >/dev/null; then
  log "forward tunnel :5002 already up"
else
  log "opening forward tunnel  :5002 -> barnacle:5002 ..."
  ssh -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -L 5002:localhost:5002 "${BARNACLE}" && log "  ok" || log "  FAILED (check ssh)"
fi

# 4) Frontend static server on :5503 (skip if Cursor / something already serves it)
if up http://127.0.0.1:5503/; then
  log "frontend :5503 already served — leaving it"
else
  log "starting fallback static server on :5503 (serving ${WEB_DIR}) ..."
  ( cd "${WEB_DIR}" && nohup python3 -m http.server 5503 --bind 127.0.0.1 >/tmp/qiita-frontend.log 2>&1 & )
fi

# 5) Status -----------------------------------------------------------------
( sleep 1 || true )
echo "==> status"
log "control plane  : $(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:8080/api/v1/auth/login 2>/dev/null || echo DOWN)  (:8080)"
log "backend        : $(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:5002/api/auth/login-url 2>/dev/null || echo DOWN)  (:5002 via forward tunnel)"
log "reverse tunnel : $(pgrep -f 'ssh.*-R 18080:localhost:8080' >/dev/null && echo UP || echo DOWN)  (barnacle:18080 -> :8080)"
log "open the app   : http://127.0.0.1:5503/qiita_explore/frontend/index.html"
echo "==> done"
