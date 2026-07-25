#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -z "${PI_SIDECAR_SECRET:-}" ]; then
  echo "PI_SIDECAR_SECRET is not set — export it (must match config.py's PI_SIDECAR_SECRET) before starting." >&2
  exit 1
fi

# PI_NODE_BIN pins the interpreter explicitly. Worth having because the node on
# PATH is not necessarily the one you think: start_barnacle.sh activates the
# conda env first, so a nodejs installed there shadows the system one — which is
# how a host with Node 22.6 on the shell ended up running the sidecar under
# conda's 20.20.
NODE_BIN="${PI_NODE_BIN:-node}"
if ! command -v "$NODE_BIN" >/dev/null 2>&1; then
  echo "node not found (looked for '$NODE_BIN'). The sidecar needs Node >= 22." >&2
  echo "Set PI_NODE_BIN=/abs/path/to/node if it isn't on PATH." >&2
  exit 1
fi

# >= 22, not >= 20. pi declares >= 22.19 and I previously treated that as a
# support-policy floor with no technical teeth — that was wrong. On Node 20 the
# undici bundled in pi's tree dies at import with
# "webidl.util.markAsUncloneable is not a function", a stack trace that says
# nothing about the Node version. 22.12 is verified working, so the check is a
# major-version floor rather than the full 22.19.
if ! "$NODE_BIN" -e 'const [a,b]=process.versions.node.split(".").map(Number); if (a<22) process.exit(1)'; then
  echo "Node $("$NODE_BIN" -v) is too old for pi — need >= 22 (pi declares >= 22.19)." >&2
  echo "  which node: $(command -v "$NODE_BIN")" >&2
  echo "If this is conda's node shadowing a newer system one:" >&2
  echo "  conda install -n qiita-web -c conda-forge 'nodejs>=22.19' -y" >&2
  echo "  rm -rf $SCRIPT_DIR/node_modules && npm --prefix $SCRIPT_DIR ci" >&2
  echo "…or point PI_NODE_BIN at the newer binary." >&2
  exit 1
fi

if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
  echo "node_modules missing — run 'npm ci' in $SCRIPT_DIR first." >&2
  exit 1
fi

# node_modules installed by a different major than the one about to run it is
# the other half of the same trap: the tree resolves, then blows up at import.
INSTALLED_WITH="$SCRIPT_DIR/node_modules/.pi-node-major"
CURRENT_MAJOR="$("$NODE_BIN" -e 'process.stdout.write(process.versions.node.split(".")[0])')"
if [ -f "$INSTALLED_WITH" ] && [ "$(cat "$INSTALLED_WITH")" != "$CURRENT_MAJOR" ]; then
  echo "node_modules was installed with Node $(cat "$INSTALLED_WITH"), now running Node $CURRENT_MAJOR." >&2
  echo "Reinstall: rm -rf $SCRIPT_DIR/node_modules && npm --prefix $SCRIPT_DIR ci" >&2
  exit 1
fi
printf '%s' "$CURRENT_MAJOR" > "$INSTALLED_WITH" 2>/dev/null || true

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

echo "Starting pi sidecar on $PI_SIDECAR_HOST:$PI_SIDECAR_PORT (flask=$FLASK_INTERNAL_URL, node $("$NODE_BIN" -v))..."
exec "$NODE_BIN" server.mjs
