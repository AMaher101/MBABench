#!/usr/bin/env bash
# Retry Test 3: TIMEOUT — 30s timeout is too short for the agent to complete
# Expected: shutdown_event fires after 30s, loop breaks with status=timeout
# Verify: Logs show timeout message, task_status=timeout in JSON (if written)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Retry Test 3: TIMEOUT ==="
echo "Tasks: retry_test_tasks.yaml"
echo "Template: template_retry_timeout.yaml"
echo "Expected: timeout after 30s (shutdown_event fires)"
echo ""

uv run python batch_automation_runner.py \
    --tasks tests/retry_tests/task_configs/retry_test_tasks.yaml \
    --runner-config tests/retry_tests/runner_configs/retry_timeout_runner.yaml
