#!/usr/bin/env bash
# Run the agent test harness in the same env as the backend (conda + Qiita config).
# Drives the real stream_agent loop from the CLI — no gunicorn, no frontend.
#   bash run_agent_harness.sh                          # interactive REPL
#   bash run_agent_harness.sh "studies on wild mice"   # one-shot
#   bash run_agent_harness.sh --model minimax-m2 "..."  # pick a model
#   bash run_agent_harness.sh --tool search_studies --args '{"keywords":["wild mice"]}'
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"

# Same branch → env/data-root resolution as start_barnacle.sh
# shellcheck source=detect_env.sh
source "$SCRIPT_DIR/detect_env.sh"

# conda activate scripts reference unset vars; nounset must be off around them
set +u
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate qiita-web
set -u

# Same Qiita DB config as start_barnacle.sh
export QIITA_CONFIG_FP="/home/d4sharma/qiita-web/qiita_config.cfg"
export QIITA_EXPERIMENT_DB_PATH="${QIITA_EXPERIMENT_DB_PATH:-$QE_DATA_ROOT/projects.db}"
export MERGE_RESULTS_DIR="${MERGE_RESULTS_DIR:-$QE_DATA_ROOT/merge_results}"
mkdir -p "$(dirname "$QIITA_EXPERIMENT_DB_PATH")" "$MERGE_RESULTS_DIR"

# Timestamped log file — every stdout byte (with ANSI stripped) is written here
LOG_FP="$SCRIPT_DIR/logs/harness_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$SCRIPT_DIR/logs"

export AGENT_DEBUG=1
export HARNESS_LOG_FP="$LOG_FP"

echo "[harness log → $LOG_FP]"
exec python agent_harness.py "$@"
