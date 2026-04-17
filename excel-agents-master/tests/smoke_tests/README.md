# Smoke Tests

Quick end-to-end tests to verify the full pipeline works for each agent. Uses trivial prompts (create sheets, write text) against a real OneDrive task.

## Prerequisites

- Browser sessions set up and logged in (see main README)
- MO13 ModelOff task exists on OneDrive at the configured path

## How to Run

```bash
# From project root:

# Individual agents
./tests/smoke_tests/run_smoke_claude.sh
./tests/smoke_tests/run_smoke_chatgpt.sh
./tests/smoke_tests/run_smoke_tabai.sh

# All 3 sequentially
./tests/smoke_tests/run_all_smoke.sh
```

## What Each Test Does

1. Navigates to OneDrive -> MO13 task folder
2. Creates/opens workbook
3. Opens the AI add-in panel
4. Sends 3 prompts:
   - Create `test_output` sheet with "test" in A1
   - Create `model_smoke` sheet with "smoke test passed" in A1
   - Create `answers_smoke` sheet with "done" in A1
5. Downloads and validates the workbook (exists, non-empty, openpyxl can open)
6. Saves JSON completion log

## What to Verify

1. Console exits with `SUCCESS`
2. `json_logs/` contains a JSON with `"task_status": "success"`
3. Downloaded Excel exists and is non-empty

## Settings

- 1 agent attempt (no retries on agent failure)
- 2 pipeline attempts (1 retry for infra flakes)
- 10 min timeout, 2 min per prompt
- No file upload (`skip_file_upload: true`)
