#!/usr/bin/env bash
# Retry Test 4: PIPELINE FAILURE — fake task names cause navigation failure
# Expected: nav_failed on every attempt, only total_attempts increments, no JSON written
# Verify: Logs show pipeline failure, total_attempts exhausted at 4, no JSON files
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Retry Test 4: PIPELINE FAILURE ==="
echo "Tasks: retry_test_fake_tasks.yaml"
echo "Template: template_retry_pipeline.yaml"
echo "Expected: nav_failed, only total_attempts increments, max_total_attempts=4 exhausted"
echo ""

uv run python batch_automation_runner.py \
    --tasks tests/retry_tests/task_configs/retry_test_fake_tasks.yaml \
    --runner-config tests/retry_tests/runner_configs/retry_pipeline_runner.yaml
