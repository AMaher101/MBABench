#!/usr/bin/env bash
# Smoke Test: ChatGPT Excel add-in
# Expected: SUCCESS, sheets test_output + model_smoke + answers_smoke created
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Smoke Test: ChatGPT Excel Add-in ==="
echo "Task: MO13 Round 1 - Sec 1 - MCQ"
echo "Expected: SUCCESS on first attempt"
echo ""

uv run python batch_automation_runner.py \
    --tasks tests/smoke_tests/task_configs/smoke_test_tasks.yaml \
    --runner-config tests/smoke_tests/runner_configs/smoke_chatgpt_runner.yaml
