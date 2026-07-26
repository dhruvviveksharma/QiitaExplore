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

BARNACLE_URL="${BARNACLE_URL:-http://localhost:${QIITA_EXPLORE_PORT:-5001}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-}"

# ── colours ────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
pass() { echo -e "${GREEN}✔ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
fail() { echo -e "${RED}✘ $*${NC}"; exit 1; }

# Treat "everything skipped" as a failure. pytest exits 0 when every test skips,
# which is how this tier reported success for months while asserting nothing.
require_ran() {
    local report="$1" label="$2"
    local ran
    ran=$(python3 - "$report" <<'PY'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
suites = root.iter("testsuite") if root.tag == "testsuites" else [root]
total = skipped = 0
for s in suites:
    total += int(s.get("tests", 0))
    skipped += int(s.get("skipped", 0))
print(total - skipped)
PY
) || fail "could not read the $label report at $report"
    if [[ "$ran" -eq 0 ]]; then
        fail "$label: 0 tests actually ran (all skipped). A skipped tier is not a passing tier — check the backend is up and that this shell has the same PI_* secrets as the server."
    fi
    pass "$label: $ran tests actually ran"
}

# ── step 1: check backend is up (skip for --unit) ───────────────────────────
if [[ "$MODE" != "--unit" ]]; then
    echo "Checking barnacle backend at $BARNACLE_URL ..."
    # /api/auth/login-url, not /api/systems: the latter is auth-protected, so
    # `curl -sf` read its 401 as "backend down" and hard-failed even against a
    # perfectly healthy server. login-url is in auth_middleware.PUBLIC_ENDPOINTS.
    if ! curl -sf "$BARNACLE_URL/api/auth/login-url" -o /dev/null --max-time 5; then
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
# --no-skip-ok: an all-skipped run is a FAILURE here, not a pass. This tier
# spent a long time reporting green while every test skipped on a 401 health
# check (TKT-046), and `pytest` exits 0 for a fully-skipped run, so the exit
# code alone could never have caught it.
BARNACLE_URL="$BARNACLE_URL" python3 -m pytest tests/e2e/ -m "e2e and not e2e_llm" -v \
    --junit-xml=/tmp/qe-e2e-deterministic.xml
require_ran /tmp/qe-e2e-deterministic.xml "deterministic e2e"
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
BARNACLE_URL="$BARNACLE_URL" python3 -m pytest tests/e2e/ -m "e2e_llm" -v \
    --junit-xml=/tmp/qe-e2e-llm.xml
require_ran /tmp/qe-e2e-llm.xml "LLM-judge e2e"
pass "LLM-judge tests passed"

echo ""
pass "All tests passed."
