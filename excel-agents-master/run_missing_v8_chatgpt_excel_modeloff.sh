#!/usr/bin/env bash
# Missing v8 runs — chatgpt_excel_agent, ModelOff (31 tasks)
# Tasks = non-deprecated modeloff with NO chatgpt_excel_agent prompt_version=8 attempt in BizbenchV1.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== Missing v8 / chatgpt_excel_agent / ModelOff (31 tasks) ==="
echo "Tasks:   tasks_configs/missing_v8_chatgpt_excel_modeloff.yaml"
echo "Runner:  runner_configs/chatgpt.yaml  (template: templates/chatgpt.yaml — prompt_version 8)"
echo

uv run python batch_automation_runner.py \
    --tasks tasks_configs/missing_v8_chatgpt_excel_modeloff.yaml \
    --runner-config runner_configs/chatgpt.yaml

echo "=== chatgpt_excel v8 modeloff complete ==="
