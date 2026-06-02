#!/usr/bin/env bash
# Run the agent test harness in the same env as the backend (conda + Qiita config).
# Drives the real stream_agent loop from the CLI — no gunicorn, no frontend.
#   bash run_agent_harness.sh                          # interactive REPL
#   bash run_agent_harness.sh "studies on wild mice"   # one-shot
#   bash run_agent_harness.sh --tool search_studies --args '{"keywords":["wild mice"]}'
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate qiita-web

# Same Qiita DB config as start_barnacle.sh
export QIITA_CONFIG_FP="/home/d4sharma/qiita-web/qiita_config.cfg"
export QIITA_EXPERIMENT_DB_PATH="${QIITA_EXPERIMENT_DB_PATH:-$HOME/.qiita-experiment/projects.db}"
mkdir -p "$(dirname "$QIITA_EXPERIMENT_DB_PATH")"

exec python agent_harness.py "$@"
