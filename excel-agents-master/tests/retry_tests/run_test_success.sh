#!/usr/bin/env bash
# Retry Test 1: SUCCESS on first attempt
# Expected: Both 'model' and 'answers' sheets are created -> validation passes -> success
# Verify: 1 JSON per task in json_logs/, task_status=success, attempt_number=0
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Retry Test 1: SUCCESS ==="
echo "Tasks: retry_test_tasks.yaml"
echo "Template: template_retry_success.yaml"
echo "Expected: success on first attempt (0 retries)"
echo ""

uv run python batch_automation_runner.py \
    --tasks tests/retry_tests/task_configs/retry_test_tasks.yaml \
    --runner-config tests/retry_tests/runner_configs/retry_success_runner.yaml
