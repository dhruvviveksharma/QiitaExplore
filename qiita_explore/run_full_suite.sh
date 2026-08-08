#!/usr/bin/env bash
# run_full_suite.sh — the single overnight entrypoint for the full e2e suite.
#
# Runs every user journey (login, chat creation, project chats, studies in a
# project, studies reaching the LLM's context, pinning) against a LIVE
# barnacle backend, as a real logged-in user. Meant to be started once and
# left running overnight — read the SUMMARY at the end (or the log file if
# you check in the morning).
#
# Usage:
#   bash qiita_explore/run_full_suite.sh              # deterministic + LLM e2e
#   bash qiita_explore/run_full_suite.sh --no-llm      # deterministic e2e only (fast, no LLM calls)
#   bash qiita_explore/run_full_suite.sh --unit        # also run the in-process unit suite first
#   bash qiita_explore/run_full_suite.sh --keep        # skip the teardown sweep (inspect leftovers)
#   bash qiita_explore/run_full_suite.sh --model NAME  # run LLM-touching turns against a specific model
#
# Required env:
#   QIITA_TEST_PAT     — a valid Qiita personal access token; the suite logs
#                        in as this account (POST /api/auth/connect) and runs
#                        every journey as it.
# Optional env:
#   BARNACLE_URL       — backend base URL (default: http://localhost:5001)
#   QIITA_TEST_ORIGIN  — Origin header sent to /api/auth/connect, must match
#                        the backend's QIITA_EXPLORE_ALLOWED_ORIGINS if set
#                        (default: http://localhost:5503)
#   OPENAI_API_KEY / API_KEY — needed for the llm_judge helper; without one,
#                        judge assertions silently pass (see parity_helpers.py)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
E2E_DIR="$BACKEND_DIR/tests/e2e"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

BARNACLE_URL="${BARNACLE_URL:-http://localhost:5001}"
export BARNACLE_URL

RUN_UNIT=false
RUN_LLM=true
TEARDOWN=true
MODEL_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-llm) RUN_LLM=false; shift ;;
        --unit)   RUN_UNIT=true; shift ;;
        --keep)   TEARDOWN=false; shift ;;
        --model)
            [[ -n "${2:-}" ]] || { echo "--model requires a value" >&2; exit 2; }
            MODEL_OVERRIDE="$2"; shift 2 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
[[ -n "$MODEL_OVERRIDE" ]] && export QIITA_TEST_MODEL="$MODEL_OVERRIDE"

RUN_ID="$(date +%Y%m%d-%H%M%S)"
export E2E_RUN_ID="$RUN_ID"
RUN_PREFIX="E2E-$RUN_ID"
LOGFILE="$LOG_DIR/full_suite_$RUN_ID.log"
PHASE1_LOG="$LOG_DIR/phase1_deterministic_$RUN_ID.log"
PHASE2_LOG="$LOG_DIR/phase2_llm_$RUN_ID.log"

# Mirror everything — this script's own output plus every phase below — to
# one combined log, so the operator can walk in the next morning and read
# one file top to bottom.
exec > >(tee -a "$LOGFILE") 2>&1

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
pass() { echo -e "${GREEN}✔ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
fail() { echo -e "${RED}✘ $*${NC}"; }
die()  { fail "$*"; exit 1; }

START_TS=$(date +%s)
echo "════════════════════════════════════════════════════════════"
echo " qiita_explore full e2e suite — run $RUN_ID"
echo " backend: $BARNACLE_URL"
echo " log:     $LOGFILE"
echo "════════════════════════════════════════════════════════════"

# ── preflight ────────────────────────────────────────────────────────────
echo ""
echo "── Preflight ──"

[[ -n "${QIITA_TEST_PAT:-}" ]] || die "QIITA_TEST_PAT is not set — export a Qiita personal access token for the account this suite should run as."

# /api/auth/login-url is one of only 3 endpoints exempt from the session
# requirement (helpers/auth_middleware.py:32-36) — a cheap up/down probe.
if ! curl -sf "$BARNACLE_URL/api/auth/login-url" -o /dev/null --max-time 5; then
    die "Backend not reachable at $BARNACLE_URL. Start it with: bash qiita_explore/start_barnacle.sh"
fi
pass "Backend is up"

AUTH_CHECK_RC=0
python3 - <<PYEOF || AUTH_CHECK_RC=$?
import sys
sys.path.insert(0, "$E2E_DIR")
from auth_client import AuthedClient, AuthError
try:
    c = AuthedClient("$BARNACLE_URL")
    print(f"authenticated as user_id={c.user_id}")
except AuthError as exc:
    print(f"AUTH FAILED: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
[[ "$AUTH_CHECK_RC" -eq 0 ]] || die "Could not authenticate with QIITA_TEST_PAT — check the token is valid and QIITA_TEST_ORIGIN matches the backend's allowlist."
pass "Authenticated with QIITA_TEST_PAT"

if [[ -z "${OPENAI_API_KEY:-}" && -z "${API_KEY:-}" ]]; then
    warn "No OPENAI_API_KEY/API_KEY set — llm_judge() silently PASSES every judge assertion (parity_helpers.py)."
    warn "LLM-judge failures in this run cannot be trusted. Set one of these env vars for a real overnight run."
fi

# ── phases ───────────────────────────────────────────────────────────────
UNIT_RC=-1
if [[ "$RUN_UNIT" == true ]]; then
    echo ""
    echo "── Unit suite (SQLite CRUD, no backend) ──"
    set +e
    bash "$BACKEND_DIR/run_tests.sh" --unit
    UNIT_RC=$?
    set -e
    [[ $UNIT_RC -eq 0 ]] && pass "Unit suite passed" || fail "Unit suite failed (see above)"
fi

echo ""
echo "── Phase 1: deterministic e2e (no LLM calls) ──"
cd "$BACKEND_DIR"
set +e
python3 -m pytest tests/e2e -m "e2e and not e2e_llm" -v 2>&1 | tee "$PHASE1_LOG"
PHASE1_RC=${PIPESTATUS[0]}
set -e
[[ $PHASE1_RC -eq 0 ]] && pass "Phase 1 passed" || fail "Phase 1 FAILED — these are real regressions, not model variance."

PHASE2_RC=-1
if [[ "$RUN_LLM" == true ]]; then
    echo ""
    echo "── Phase 2: LLM-touching e2e (slow — this is the overnight part) ──"
    set +e
    python3 -m pytest tests/e2e -m "e2e_llm" -v 2>&1 | tee "$PHASE2_LOG"
    PHASE2_RC=${PIPESTATUS[0]}
    set -e
    [[ $PHASE2_RC -eq 0 ]] && pass "Phase 2 passed" || warn "Phase 2 had failures — investigate; some may be model variance, not regressions."
else
    warn "Skipping Phase 2 (--no-llm)"
fi

# ── teardown ─────────────────────────────────────────────────────────────
if [[ "$TEARDOWN" == true ]]; then
    echo ""
    echo "── Teardown sweep (prefix: $RUN_PREFIX) ──"
    python3 "$E2E_DIR/teardown_sweep.py" "$RUN_PREFIX" || warn "Teardown sweep failed — leftover $RUN_PREFIX projects/chats may remain, clean up manually."
else
    warn "Skipping teardown (--keep) — $RUN_PREFIX projects/chats left in place for inspection."
fi

# ── summary ──────────────────────────────────────────────────────────────
END_TS=$(date +%s)
DURATION=$((END_TS - START_TS))

summary_line() {
    # pytest's own final line ("N passed, M failed in Xs") is the most
    # trustworthy count — pull it rather than re-deriving one.
    tail -20 "$1" 2>/dev/null | grep -E "passed|failed|error|no tests ran" | tail -1
}

echo ""
echo "════════════════════════════════════════════════════════════"
echo " SUMMARY — run $RUN_ID (${DURATION}s)"
echo "════════════════════════════════════════════════════════════"
[[ "$RUN_UNIT" == true ]] && echo " Unit:              $([[ $UNIT_RC -eq 0 ]] && echo PASSED || echo FAILED)"
echo " Phase 1 (deterministic): $([[ $PHASE1_RC -eq 0 ]] && echo PASSED || echo FAILED)  — $(summary_line "$PHASE1_LOG")"
if [[ "$RUN_LLM" == true ]]; then
    echo " Phase 2 (LLM-touching):  $([[ $PHASE2_RC -eq 0 ]] && echo PASSED || echo "FAILED (review — may be model variance)")  — $(summary_line "$PHASE2_LOG")"
fi
echo ""
echo " Full log: $LOGFILE"
[[ $PHASE1_RC -ne 0 ]] && echo -e " ${RED}❌ Phase 1 failures are real regressions — check them first.${NC}"
[[ "$RUN_LLM" == true && $PHASE2_RC -ne 0 ]] && echo -e " ${YELLOW}⚠  Phase 2 had failures — review before assuming a regression.${NC}"
echo "════════════════════════════════════════════════════════════"

# Exit non-zero only on deterministic failure — that's the signal that
# something is actually broken, not model variance.
exit $PHASE1_RC
