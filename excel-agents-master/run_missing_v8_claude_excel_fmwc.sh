#!/usr/bin/env bash
# Missing v8 runs — claude_excel_agent, FMWC (43 tasks)
# Tasks = non-deprecated fmwc with NO claude_excel_agent prompt_version=8 attempt in BizbenchV1.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== Missing v8 / claude_excel_agent / FMWC (43 tasks) ==="
echo "Tasks:   tasks_configs/missing_v8_claude_excel_fmwc.yaml"
echo "Runner:  runner_configs/claude.yaml  (template: templates/claude.yaml — prompt_version 8)"
echo

uv run python batch_automation_runner.py \
    --tasks tasks_configs/missing_v8_claude_excel_fmwc.yaml \
    --runner-config runner_configs/claude.yaml

echo "=== claude_excel v8 fmwc complete ==="
