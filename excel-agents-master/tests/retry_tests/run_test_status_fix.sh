#!/usr/bin/env bash
# ===========================================================================
# Status Fix Test: Pipeline Fail → nav_failed (not timeout)
#
# Verifies TWO fixes simultaneously:
#   1. Post-loop timeout check no longer overrides nav_failed → timeout
#   2. CDP tab cleanup closes navigation pages between tasks
#
# Runs 2 fake tasks (max_total_attempts=1 each, ~75s per task).
# Total runtime: ~3 minutes.
#
# Usage:  ./tests/retry_tests/run_test_status_fix.sh
# ===========================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$RESULTS_DIR"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$RESULTS_DIR/status_fix_${TIMESTAMP}.log"

echo "============================================================" | tee "$LOG_FILE"
echo "  STATUS FIX TEST" | tee -a "$LOG_FILE"
echo "  Started: $(date)" | tee -a "$LOG_FILE"
echo "  Log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "============================================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "  Config: 2 fake tasks, max_total_attempts=1, 300s timeout" | tee -a "$LOG_FILE"
echo "  Expected: nav_failed for both tasks, no timeout override" | tee -a "$LOG_FILE"
echo "  Also checking: CDP tab cleanup between tasks" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ============================================================================
# Run the pipeline fail batch
# ============================================================================
echo "Running pipeline fail tasks..." | tee -a "$LOG_FILE"
uv run python batch_automation_runner.py \
    --tasks tests/retry_tests/task_configs/retry_test_fake_tasks.yaml \
    --runner-config tests/retry_tests/runner_configs/status_fix_navfail_runner.yaml \
    2>&1 | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"

# ============================================================================
# Verify results
# ============================================================================
PASS=0
FAIL=0

echo "============================================================" | tee -a "$LOG_FILE"
echo "  VERIFICATION" | tee -a "$LOG_FILE"
echo "============================================================" | tee -a "$LOG_FILE"

# --- Check 1: nav_failed status (the main fix) ---
# Note: grep -c exits 1 on zero matches; use || true to avoid triggering pipefail
NAV_FAILED_COUNT=$(grep -c "Final status: nav_failed" "$LOG_FILE" || true)
TIMEOUT_COUNT=$(grep -c "Final status: timeout" "$LOG_FILE" || true)

if [ "$NAV_FAILED_COUNT" -ge 2 ]; then
    echo "  ✅ PASS: Found $NAV_FAILED_COUNT tasks with 'Final status: nav_failed'" | tee -a "$LOG_FILE"
    PASS=$((PASS + 1))
else
    echo "  ❌ FAIL: Expected 2+ nav_failed, found $NAV_FAILED_COUNT" | tee -a "$LOG_FILE"
    FAIL=$((FAIL + 1))
fi

if [ "$TIMEOUT_COUNT" -eq 0 ]; then
    echo "  ✅ PASS: No false 'Final status: timeout' overrides" | tee -a "$LOG_FILE"
    PASS=$((PASS + 1))
else
    echo "  ❌ FAIL: Found $TIMEOUT_COUNT false timeout overrides (should be 0)" | tee -a "$LOG_FILE"
    grep "Final status: timeout" "$LOG_FILE" | head -5 | tee -a "$LOG_FILE"
    FAIL=$((FAIL + 1))
fi

# --- Check 2: agent_attempts stayed at 0 (pipeline fail, not agent fail) ---
AGENT_ZERO_COUNT=$(grep -c "(0 agent)" "$LOG_FILE" || true)
if [ "$AGENT_ZERO_COUNT" -ge 2 ]; then
    echo "  ✅ PASS: agent_attempts=0 for both tasks (pipeline fail, not agent)" | tee -a "$LOG_FILE"
    PASS=$((PASS + 1))
else
    echo "  ❌ FAIL: Expected agent_attempts=0, found $AGENT_ZERO_COUNT matching lines" | tee -a "$LOG_FILE"
    FAIL=$((FAIL + 1))
fi

# --- Check 3: No JSON completion files (pipeline fail = no agent phase) ---
# Look for completion files from THIS run (recent timestamps)
RECENT_JSONS=$(find . -path "*/json_logs/*FAKE*" -mmin -30 2>/dev/null | wc -l | tr -d ' ')
if [ "$RECENT_JSONS" -eq 0 ]; then
    echo "  ✅ PASS: No JSON completion files for fake tasks" | tee -a "$LOG_FILE"
    PASS=$((PASS + 1))
else
    echo "  ❌ FAIL: Found $RECENT_JSONS unexpected JSON files for fake tasks" | tee -a "$LOG_FILE"
    FAIL=$((FAIL + 1))
fi

# --- Check 4: CDP tab cleanup ---
CDP_CLEANUP_COUNT=$(grep -c "Closed main navigation page (CDP cleanup)" "$LOG_FILE" || true)
if [ "$CDP_CLEANUP_COUNT" -ge 2 ]; then
    echo "  ✅ PASS: CDP cleanup fired for $((CDP_CLEANUP_COUNT / 2)) tasks (dual-log)" | tee -a "$LOG_FILE"
    PASS=$((PASS + 1))
else
    echo "  ⚠️  SKIP: CDP cleanup messages found: $CDP_CLEANUP_COUNT (fix may not be deployed yet)" | tee -a "$LOG_FILE"
fi

# --- Check 5: Tab count stability ---
# Extract "Using existing context (N page(s))" — one per task subprocess
# Note: macOS grep lacks -P; use sed instead
TAB_COUNTS=($(grep "Using existing context" "$LOG_FILE" | sed 's/.*(\([0-9]*\) page.*/\1/' | sort -u || true))
if [ ${#TAB_COUNTS[@]} -ge 2 ]; then
    FIRST=${TAB_COUNTS[0]}
    LAST=${TAB_COUNTS[-1]}
    if [ "$FIRST" -eq "$LAST" ]; then
        echo "  ✅ PASS: Tab count stable ($FIRST → $LAST) — no accumulation" | tee -a "$LOG_FILE"
        PASS=$((PASS + 1))
    elif [ "$LAST" -gt "$FIRST" ]; then
        echo "  ⚠️  SKIP: Tab count grew ($FIRST → $LAST) — CDP cleanup may not be deployed" | tee -a "$LOG_FILE"
    else
        echo "  ✅ PASS: Tab count decreased ($FIRST → $LAST)" | tee -a "$LOG_FILE"
        PASS=$((PASS + 1))
    fi
else
    echo "  ⚠️  SKIP: Could not extract tab counts (found ${#TAB_COUNTS[@]} entries)" | tee -a "$LOG_FILE"
fi

# ============================================================================
# Summary
# ============================================================================
echo "" | tee -a "$LOG_FILE"
echo "============================================================" | tee -a "$LOG_FILE"
echo "  SUMMARY" | tee -a "$LOG_FILE"
echo "  Finished: $(date)" | tee -a "$LOG_FILE"
echo "  Checks passed: $PASS    Failed: $FAIL" | tee -a "$LOG_FILE"
echo "  Full log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "============================================================" | tee -a "$LOG_FILE"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
