#!/usr/bin/env bash
# Rerun the chatgpt_excel_agent v8 attempt for Will-You-Be-Mine-zmexkc
# (task_id=256, fmwc), which previously failed across 12 tries because
# the agent's upload step couldn't load the task's 3rd attached file.
#
# Fix shipped on main commit 8c3367e / excel-agents-master commit 3bcc0ad:
#   chatgpt_core.upload_files: Escape stale popovers, retry the menuitem
#   path up to 3 times, scope hidden-input fallback to the ChatGPT frame.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== Rerun chatgpt_excel_agent v8 / fmwc / Will-You-Be-Mine-zmexkc ==="
echo "Tasks:   tasks_configs/rerun_multifile_v8_chatgpt_excel_fmwc.yaml"
echo "Runner:  runner_configs/chatgpt.yaml  (template: templates/chatgpt_excel.yaml — prompt_version 8)"
echo

uv run python batch_automation_runner.py \
    --tasks tasks_configs/rerun_multifile_v8_chatgpt_excel_fmwc.yaml \
    --runner-config runner_configs/chatgpt.yaml

echo
echo "=== rerun complete ==="
