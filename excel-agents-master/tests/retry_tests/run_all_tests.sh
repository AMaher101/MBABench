#!/usr/bin/env bash
# ============================================================================
# Run ALL retry tests sequentially and log results to a single report file.
#
# Usage:  ./tests/retry_tests/run_all_tests.sh
# Output: tests/retry_tests/results/test_run_<timestamp>.log
# ============================================================================
set -uo pipefail  # no -e: we want to keep going after failures

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Create results directory
RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$RESULTS_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$RESULTS_DIR/test_run_${TIMESTAMP}.log"

# Track pass/fail
PASSED=0
FAILED=0
TOTAL=4

# Helper: run a single test, capture exit code and output
run_test() {
    local test_name="$1"
    local test_num="$2"
    local script="$3"

    echo ""
    echo "================================================================" | tee -a "$LOG_FILE"
    echo "  TEST $test_num/$TOTAL: $test_name" | tee -a "$LOG_FILE"
    echo "  Started: $(date)" | tee -a "$LOG_FILE"
    echo "================================================================" | tee -a "$LOG_FILE"

    # Run the test, tee output to both console and log
    bash "$script" 2>&1 | tee -a "$LOG_FILE"
    local exit_code=${PIPESTATUS[0]}

    echo "" | tee -a "$LOG_FILE"
    if [ $exit_code -eq 0 ]; then
        echo "  RESULT: COMPLETED (exit code 0)" | tee -a "$LOG_FILE"
        PASSED=$((PASSED + 1))
    else
        echo "  RESULT: FAILED (exit code $exit_code)" | tee -a "$LOG_FILE"
        FAILED=$((FAILED + 1))
    fi
    echo "  Finished: $(date)" | tee -a "$LOG_FILE"
    echo "----------------------------------------------------------------" | tee -a "$LOG_FILE"

    # Brief pause between tests to let browser settle
    sleep 5
}

# ============================================================================
# Header
# ============================================================================
echo "============================================================" | tee "$LOG_FILE"
echo "  RETRY TEST SUITE" | tee -a "$LOG_FILE"
echo "  Started: $(date)" | tee -a "$LOG_FILE"
echo "  Log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "============================================================" | tee -a "$LOG_FILE"

# ============================================================================
# Run all 4 tests
# ============================================================================
run_test "SUCCESS (expect: success on 1st attempt)" \
    1 "$SCRIPT_DIR/run_test_success.sh"

run_test "MISSING SHEETS (expect: missing_sheets, exhausts 2 agent attempts)" \
    2 "$SCRIPT_DIR/run_test_missing_sheets.sh"

run_test "TIMEOUT (expect: timeout with 10s limit)" \
    3 "$SCRIPT_DIR/run_test_timeout.sh"

run_test "PIPELINE FAIL (expect: nav_failed, exhausts 2 total attempts)" \
    4 "$SCRIPT_DIR/run_test_pipeline_fail.sh"

# ============================================================================
# Summary
# ============================================================================
echo "" | tee -a "$LOG_FILE"
echo "============================================================" | tee -a "$LOG_FILE"
echo "  SUMMARY" | tee -a "$LOG_FILE"
echo "  Finished: $(date)" | tee -a "$LOG_FILE"
echo "  Completed: $PASSED/$TOTAL    Failed: $FAILED/$TOTAL" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "  Expected outcomes per test:" | tee -a "$LOG_FILE"
echo "    1. SUCCESS:        task_status=success, 0 retries" | tee -a "$LOG_FILE"
echo "    2. MISSING SHEETS: task_status=missing_sheets, 2 agent attempts" | tee -a "$LOG_FILE"
echo "    3. TIMEOUT:        task_status=timeout" | tee -a "$LOG_FILE"
echo "    4. PIPELINE FAIL:  nav_failed, no JSON files produced" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "  Full log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "============================================================" | tee -a "$LOG_FILE"

# Also dump any JSON completion files created during this run
echo "" | tee -a "$LOG_FILE"
echo "  JSON completion files in json_logs/:" | tee -a "$LOG_FILE"
# JSON logs are now stored in {date}_{label}/json_logs/ run directories
find . -path "*/json_logs/completion_*.json" -mmin -120 -print 2>/dev/null | sort | tee -a "$LOG_FILE"
if [ $? -ne 0 ] || [ -z "$(find . -path "*/json_logs/completion_*.json" -mmin -120 -print 2>/dev/null)" ]; then
    echo "    (no recent completion files found)" | tee -a "$LOG_FILE"
fi
echo "" | tee -a "$LOG_FILE"
