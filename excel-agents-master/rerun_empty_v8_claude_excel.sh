#!/usr/bin/env bash
# Rerun the 2 claude_excel_agent v8 attempts that produced empty workbooks (only ['Sheet1'], 0 cells).
# Original attempt rows in BizbenchV1 task_attempts:
#   id=5491  task_id=107  fmwc      Book-Your-Flight-3hiskd
#   id=4894  task_id=396  modeloff  MO18 Round 2 - Sec 2 - Winning Ways
# Both were marked agent_failed=true post-hoc on 2026-05-04 with reason
# "Empty workbook downloaded — only ['Sheet1'] saved (0 cells). Marked failed post-hoc 2026-05-04."

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== Rerun empty v8 / claude_excel_agent (2 tasks across fmwc + modeloff) ==="
echo "Runner:  runner_configs/claude.yaml  (template: templates/claude_excel.yaml — prompt_version 8)"
echo

echo "--- fmwc (1 task) ---"
uv run python batch_automation_runner.py \
    --tasks tasks_configs/rerun_empty_v8_claude_excel_fmwc.yaml \
    --runner-config runner_configs/claude.yaml

echo
echo "--- modeloff (1 task) ---"
uv run python batch_automation_runner.py \
    --tasks tasks_configs/rerun_empty_v8_claude_excel_modeloff.yaml \
    --runner-config runner_configs/claude.yaml

echo
echo "=== rerun complete ==="
