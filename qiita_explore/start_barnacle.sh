#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"

# Branch → deployment (master/5001) or dev (else/5002) + /ddn_scratch data root
# shellcheck source=detect_env.sh
source "$SCRIPT_DIR/detect_env.sh"

# conda activate scripts reference unset vars; nounset must be off around them
set +u
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate qiita-web
set -u

# Point Qiita at the repo's config file (has correct qiita-db-rc credentials)
export QIITA_CONFIG_FP="/home/d4sharma/qiita-web/qiita_config.cfg"

export QIITA_EXPERIMENT_DB_PATH="${QIITA_EXPERIMENT_DB_PATH:-$QE_DATA_ROOT/projects.db}"
export MERGE_RESULTS_DIR="${MERGE_RESULTS_DIR:-$QE_DATA_ROOT/merge_results}"
mkdir -p "$(dirname "$QIITA_EXPERIMENT_DB_PATH")" "$MERGE_RESULTS_DIR"

echo "Frontend uses Babel standalone (runtime transpilation) — no compile step needed."

cd "$SCRIPT_DIR/backend"
echo "Starting gunicorn on port $QE_PORT (4 workers, 2 threads each)..."
exec gunicorn -w 4 --threads 2 -b 0.0.0.0:$QE_PORT \
  --timeout 120 --graceful-timeout 30 \
  --worker-class gthread \
  --log-level info \
  run:app
