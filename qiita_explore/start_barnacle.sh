#!/usr/bin/env bash
# The one backend command. Starts Flask (gunicorn) and the pi sidecar, in that
# order — the sidecar fetches its model roster from Flask at boot — and holds
# both in the foreground. Ctrl-C stops both; if either dies, the other is torn
# down rather than leaving half a stack up.
#
#   bash qiita_explore/start_barnacle.sh
#   PI_SKIP_SIDECAR=1 bash qiita_explore/start_barnacle.sh   # Flask alone
#   QIITA_EXPLORE_PORT=5002 bash ...                         # dev port
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR/backend"

# Activate the conda env when conda is present. On hosts without it (e.g. a
# Mac running the app tier against a tunnelled Postgres), fall through to
# whatever python3/gunicorn is already on PATH rather than hard-failing.
if command -v conda >/dev/null 2>&1; then
  # `set -eu` off across the activation. conda's activate.d/deactivate.d hooks
  # are not written to survive `set -u` — barnacle's
  # deactivate-gcc_linux-64.sh reads $_CONDA_PYTHON_SYSCONFIGDATA_NAME_USED
  # unguarded and killed this script with "unbound variable" before it started
  # anything. Which hooks run depends on the env you launch from, so this fails
  # for one person and not another.
  set +eu
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate qiita-web
  CONDA_RC=$?
  set -eu
  if [ "$CONDA_RC" -ne 0 ]; then
    echo "conda activate qiita-web failed (exit $CONDA_RC)." >&2
    exit 1
  fi
else
  echo "conda not found — using the ambient python3 environment."
fi

# Point Qiita at the config file supplying PostgreSQL credentials. Default is
# barnacle's; overridable from the environment so the same script runs on a
# host whose config lives elsewhere (previously a hardcoded path, which meant
# editing this file was the only way to move it — see docs/09-operations.md).
export QIITA_CONFIG_FP="${QIITA_CONFIG_FP:-/home/d4sharma/qiita-web/qiita_config.cfg}"
if [ ! -f "$QIITA_CONFIG_FP" ]; then
  echo "QIITA_CONFIG_FP does not exist: $QIITA_CONFIG_FP" >&2
  echo "Set it to this host's Qiita config file (PostgreSQL credentials)." >&2
  exit 1
fi

export QIITA_EXPERIMENT_DB_PATH="${QIITA_EXPERIMENT_DB_PATH:-$HOME/.qiita-experiment/projects.db}"
mkdir -p "$(dirname "$QIITA_EXPERIMENT_DB_PATH")"

# We run gunicorn from backend/, so the vendored qiita_db/qiita_core at the repo
# root are only importable if they were pip-installed into the env (true on
# barnacle, not elsewhere — otherwise every worker dies with
# "ModuleNotFoundError: No module named 'qiita_db'"). Only fall back when the
# import actually fails, so barnacle's resolution order is untouched.
if ! python3 -c "import qiita_db" >/dev/null 2>&1; then
  export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
  echo "qiita_db not installed in this env — added $REPO_ROOT to PYTHONPATH."
fi

PORT="${QIITA_EXPLORE_PORT:-5001}"

# Prefer the gunicorn binary; fall back to `python3 -m gunicorn` when it was
# pip-installed with --user and its bin dir isn't on PATH.
if command -v gunicorn >/dev/null 2>&1; then
  GUNICORN=(gunicorn)
else
  GUNICORN=(python3 -m gunicorn)
fi

FLASK_PID=""
SIDECAR_PID=""
# Set (to a short reason) right before the die-together loop below breaks out
# on a detected crash — distinguishes that from an operator's Ctrl-C/TERM, so
# shutdown() can report failure. Without this, shutdown() always exited 0:
# a crashed Flask or sidecar looked identical, to any script or supervisor
# checking this command's exit code, to a clean operator-requested stop.
CRASHED=""

shutdown() {
  # Capture the status that triggered this FIRST — it is $? until any other
  # command runs. The trap used to end in a bare `exit 0`, which silently
  # replaced the status of every `exit 1` reached after the trap was installed:
  # a refused start (port busy, sidecar secret missing) and a gunicorn that
  # died during boot all reported success to whatever ran this script. CRASHED
  # only ever covered the die-together loop at the bottom.
  local rc="${1:-$?}"
  trap - INT TERM EXIT
  echo
  echo "Stopping…"
  # Sidecar first: it calls back into Flask, so tearing Flask down underneath a
  # live turn just produces noise in the log.
  [ -n "$SIDECAR_PID" ] && kill "$SIDECAR_PID" 2>/dev/null || true
  [ -n "$FLASK_PID" ]   && kill "$FLASK_PID"   2>/dev/null || true
  wait 2>/dev/null || true
  [ -n "$CRASHED" ] && rc=1
  exit "$rc"
}
# An operator's Ctrl-C is a clean stop, so INT/TERM pass an explicit 0 rather
# than inheriting whatever the interrupted command happened to return.
trap 'shutdown 0' INT TERM
trap shutdown EXIT

# ── pi configuration, resolved BEFORE gunicorn ──────────────────────────────
# Order matters: gunicorn reads PI_BACKEND_* at import, so the decision about
# which chat runtime this process serves has to be made before it is spawned.
# Validating first also means a misconfiguration costs one clear message rather
# than four worker tracebacks.
#
# The sidecar is Node and cannot read .env itself, so its secrets are exported
# here. Parsed with python-dotenv (a declared backend dep, requirements.txt)
# rather than shell word-splitting, which mangles the existing
# `API_KEY = "SQ…"` line — spaces around = and quotes around the value.
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  eval "$(python3 -c "
import shlex
from dotenv import dotenv_values
wanted = ('PI_SIDECAR_SECRET','PI_SIDECAR_PORT','PI_SIDECAR_HOST','PI_NODE_BIN','API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY')
for k, v in dotenv_values('$ENV_FILE').items():
    if v and k in wanted:
        print(f'export {k}={shlex.quote(v)}')
")"
fi

START_SIDECAR=1
if [ "${PI_SKIP_SIDECAR:-}" = "1" ]; then
  # An explicit "Flask alone" request. pi is the default runtime, so this must
  # also switch the app off it — otherwise this flag produces a backend that
  # boots cleanly and 500s on every chat turn, which is the opposite of what
  # anyone reaches for it to do.
  echo "PI_SKIP_SIDECAR=1 — Flask alone, forcing the legacy chat path."
  export PI_BACKEND_GLOBAL=false
  export PI_BACKEND_PROJECT=false
  START_SIDECAR=0
elif [ -z "${PI_SIDECAR_SECRET:-}" ]; then
  # Hard failure now that pi is the default runtime. This used to print
  # "legacy chat path only" and carry on, which was true while PI_BACKEND_*
  # defaulted false and is a lie now: Flask would come up, the UI would load,
  # and every chat turn would 401. run.py refuses to boot in this state too —
  # this check just puts the reason on screen before four workers print it.
  echo "PI_SIDECAR_SECRET unset, but pi is the default chat backend." >&2
  echo "Set it in $ENV_FILE (openssl rand -base64 32), or run the legacy path with:" >&2
  echo "  PI_SKIP_SIDECAR=1 bash qiita_explore/start_barnacle.sh" >&2
  exit 1
fi

# Refuse to hand the port to gunicorn if something already holds it. The
# readiness probe below cannot tell "my child is up" from "a stale process
# answers on this port" — seen live: an orphaned gunicorn held :5002, the new
# one logged "Connection in use" five times and died, the probe reported ok
# because the OLD process replied, and the script went on to wire a sidecar to
# a Flask it had not started.
if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use — refusing to start a second backend." >&2
  echo "Inspect it with:  lsof -nP -iTCP:$PORT -sTCP:LISTEN" >&2
  echo "Stop it with:     pkill -f \"gunicorn.*:$PORT\"" >&2
  exit 1
fi

echo "Starting gunicorn on port $PORT (4 workers, 2 threads each)..."
# --timeout 300 (was 120): a pi-backed turn can include compaction plus
# several tool round-trips to the sidecar; 120s was already tight for a
# single slow NRP completion and would kill the worker mid-SSE. Matches the
# 300s timeout already used for the NRP/Anthropic clients in config.py.
"${GUNICORN[@]}" -w 4 --threads 2 -b "0.0.0.0:$PORT" \
  --timeout 300 --graceful-timeout 30 \
  --worker-class gthread \
  --log-level info \
  run:app &
FLASK_PID=$!

# Wait for Flask to actually serve before starting the sidecar. 401 is the
# healthy answer — /api/global-chats is auth-protected, so anything other than
# a connection failure means the app is up.
echo -n "Waiting for Flask to accept connections"
for _ in $(seq 1 60); do
  if ! kill -0 "$FLASK_PID" 2>/dev/null; then
    echo; echo "gunicorn exited during startup — see the traceback above." >&2
    exit 1
  fi
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/api/global-chats" 2>/dev/null; then
    echo " ok"
    break
  fi
  echo -n "."
  sleep 1
done

# ── pi sidecar ──────────────────────────────────────────────────────────────
# Started only now: it fetches its model roster from Flask at boot, so Flask has
# to be answering first. Everything it needs was resolved and validated above.
if [ "$START_SIDECAR" = "1" ]; then
  export FLASK_INTERNAL_URL="${FLASK_INTERNAL_URL:-http://127.0.0.1:$PORT}"
  echo "Starting pi sidecar (flask=$FLASK_INTERNAL_URL)..."
  bash "$SCRIPT_DIR/pi_sidecar/start_sidecar.sh" &
  SIDECAR_PID=$!
fi

echo
echo "Backend up. Ctrl-C stops everything."

# Exit as soon as EITHER child dies, so a half-up stack (which is what made
# today's "is pi even being used?" hard to answer) can't sit around unnoticed.
#
# Polled rather than `wait -n`: that needs bash >= 4.3, and macOS still ships
# 3.2, where it fails and silently degrades to plain `wait` — i.e. exactly the
# half-up state this is meant to prevent, on the machine most likely to be
# running the script by hand.
while :; do
  if [ -n "$FLASK_PID" ] && ! kill -0 "$FLASK_PID" 2>/dev/null; then
    echo "gunicorn exited — stopping the sidecar too." >&2
    CRASHED="gunicorn exited"
    break
  fi
  if [ -n "$SIDECAR_PID" ] && ! kill -0 "$SIDECAR_PID" 2>/dev/null; then
    echo "pi sidecar exited — stopping Flask too. See the traceback above." >&2
    CRASHED="pi sidecar exited"
    break
  fi
  sleep 2
done
shutdown
