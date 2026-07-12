#!/usr/bin/env bash
# run_tests.sh — run the full parity test suite from the backend directory.
#
# Usage:
#   bash run_tests.sh           # deterministic tests only (no LLM)
#   bash run_tests.sh --llm     # also run LLM-judge tests (slow, run overnight)
#   bash run_tests.sh --all     # unit + deterministic + LLM
#   bash run_tests.sh --unit    # unit tests only (no backend required)
#
# Environment:
#   BARNACLE_URL  — override backend URL (default: http://localhost:5001)

set -euo pipefail

BARNACLE_URL="${BARNACLE_URL:-http://localhost:5001}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-}"

# ── colours ────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
pass() { echo -e "${GREEN}✔ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
fail() { echo -e "${RED}✘ $*${NC}"; exit 1; }

# ── step 1: check backend is up (skip for --unit) ───────────────────────────
if [[ "$MODE" != "--unit" ]]; then
    echo "Checking barnacle backend at $BARNACLE_URL ..."
    if ! curl -sf "$BARNACLE_URL/api/systems" -o /dev/null --max-time 5; then
        fail "Backend not reachable at $BARNACLE_URL. Start it with: bash qiita_explore/start_barnacle.sh"
    fi
    pass "Backend is up"
fi

# ── step 2: unit tests (always fast, no backend needed) ─────────────────────
echo ""
echo "════════════════════════════════════════"
echo " Unit tests (SQLite CRUD)"
echo "════════════════════════════════════════"
python3 -m pytest tests/ -m "not e2e" -v
pass "Unit tests passed"

if [[ "$MODE" == "--unit" ]]; then
    echo ""
    pass "Done (unit only)"
    exit 0
fi

# ── step 3: deterministic e2e tests (no LLM) ────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo " Deterministic e2e tests"
echo " (backend + Qiita DB, no LLM calls)"
echo "════════════════════════════════════════"
BARNACLE_URL="$BARNACLE_URL" python3 -m pytest tests/e2e/ -m "e2e and not e2e_llm" -v
pass "Deterministic e2e tests passed"

if [[ "$MODE" != "--llm" && "$MODE" != "--all" ]]; then
    echo ""
    pass "Done. To also run LLM-judge tests: bash run_tests.sh --llm"
    exit 0
fi

# ── step 4: LLM-judge tests (slow, run overnight) ───────────────────────────
echo ""
echo "════════════════════════════════════════"
echo " LLM-judge e2e tests"
echo " (kimi evaluates assistant responses)"
echo "════════════════════════════════════════"
BARNACLE_URL="$BARNACLE_URL" python3 -m pytest tests/e2e/ -m "e2e_llm" -v
pass "LLM-judge tests passed"

echo ""
pass "All tests passed."
