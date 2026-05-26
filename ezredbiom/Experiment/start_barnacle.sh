#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate qiita-web

# Point Qiita at the repo's config file (has correct qiita-db-rc credentials)
export QIITA_CONFIG_FP="/home/d4sharma/qiita-web/qiita_config.cfg"

export QIITA_EXPERIMENT_DB_PATH="${QIITA_EXPERIMENT_DB_PATH:-$HOME/.qiita-experiment/projects.db}"
mkdir -p "$(dirname "$QIITA_EXPERIMENT_DB_PATH")"

echo "Frontend uses Babel standalone (runtime transpilation) — no compile step needed."

cd "$SCRIPT_DIR/backend"
echo "Starting gunicorn on port 5002 (4 workers, 2 threads each)..."
exec gunicorn -w 4 --threads 2 -b 0.0.0.0:5002 \
  --timeout 120 --graceful-timeout 30 \
  --worker-class gthread \
  --log-level info \
  run:app
