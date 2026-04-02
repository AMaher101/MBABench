#!/usr/bin/env bash
# Retry Test 2: MISSING_SHEETS — agent creates wrong sheet names
# Expected: Validation fails (no model/answers sheets) -> retries -> exhausts max_agent_attempts=2
# Verify: 2 JSONs per task (attempt 0 deprecated, attempt 1 final), task_status=missing_sheets
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Retry Test 2: MISSING_SHEETS ==="
echo "Tasks: retry_test_tasks.yaml"
echo "Template: template_retry_missing.yaml"
echo "Expected: missing_sheets after 2 agent attempts"
echo ""

uv run python batch_automation_runner.py \
    --tasks tests/retry_tests/task_configs/retry_test_tasks.yaml \
    --runner-config tests/retry_tests/runner_configs/retry_missing_runner.yaml
