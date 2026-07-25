#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -z "${PI_SIDECAR_SECRET:-}" ]; then
  echo "PI_SIDECAR_SECRET is not set — export it (must match config.py's PI_SIDECAR_SECRET) before starting." >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "node not found. The sidecar needs Node >= 20 (pi declares >= 22.19, but nothing in it requires past 22.12)." >&2
  exit 1
fi
node -e 'if (Number(process.versions.node.split(".")[0]) < 20) process.exit(1)' || {
  echo "Node $(node -v) is too old — need >= 20 (undici, global fetch)." >&2
  exit 1
}

if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
  echo "node_modules missing — run 'npm ci' in $SCRIPT_DIR first." >&2
  exit 1
fi

export PI_SIDECAR_PORT="${PI_SIDECAR_PORT:-5100}"
# Loopback by default so a dev box never exposes the sidecar. In the deployed
# topology the sidecar runs on the intermediate node and Flask calls it from
# barnacle, so that deployment MUST set PI_SIDECAR_HOST=0.0.0.0 (and firewall
# the port to barnacle only) or Flask cannot reach it at all.
export PI_SIDECAR_HOST="${PI_SIDECAR_HOST:-127.0.0.1}"
# Where the sidecar calls back for tool execution. Cross-machine in deployment:
# point this at barnacle, and set PI_ALLOWED_TOOL_CALLERS there to this host.
export FLASK_INTERNAL_URL="${FLASK_INTERNAL_URL:-http://127.0.0.1:5001}"
export PI_SIDECAR_STATE_DIR="${PI_SIDECAR_STATE_DIR:-$SCRIPT_DIR/.state}"

echo "Starting pi sidecar on $PI_SIDECAR_HOST:$PI_SIDECAR_PORT (flask=$FLASK_INTERNAL_URL)..."
exec node server.mjs
