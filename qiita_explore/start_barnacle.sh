#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"

# Activate the conda env when conda is present. On hosts without it (e.g. a
# Mac running the app tier against a tunnelled Postgres), fall through to
# whatever python3/gunicorn is already on PATH rather than hard-failing.
if command -v conda >/dev/null 2>&1; then
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate qiita-web
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

echo "Frontend uses Babel standalone (runtime transpilation) — no compile step needed."

cd "$SCRIPT_DIR/backend"

# Prefer the gunicorn binary; fall back to `python3 -m gunicorn` when it was
# pip-installed with --user and its bin dir isn't on PATH.
if command -v gunicorn >/dev/null 2>&1; then
  GUNICORN=(gunicorn)
else
  GUNICORN=(python3 -m gunicorn)
fi

PORT="${QIITA_EXPLORE_PORT:-5001}"
echo "Starting gunicorn on port $PORT (4 workers, 2 threads each)..."
# --timeout 300 (was 120): a pi-backed turn can include compaction plus
# several tool round-trips to the sidecar; 120s was already tight for a
# single slow NRP completion and would kill the worker mid-SSE. Matches the
# 300s timeout already used for the NRP/Anthropic clients in config.py.
exec "${GUNICORN[@]}" -w 4 --threads 2 -b "0.0.0.0:$PORT" \
  --timeout 300 --graceful-timeout 30 \
  --worker-class gthread \
  --log-level info \
  run:app
