# Smoke Tests

Quick end-to-end tests that verify the full pipeline works for each agent:
launch the browser, navigate to a OneDrive folder, open a workbook, drive the
AI add-in panel, download the result, and validate it.

## Prerequisites

These tests run the **real** pipeline against a real OneDrive folder, so they
need exactly the same setup as a normal run plus a small amount of test data
on your OneDrive account.

1. **Browser sessions are set up and logged in.** Follow the Setup section in
   the main [README](../../README.md) (`scripts/setup_firefox.sh` and
   `scripts/setup_chrome.sh`).

2. **Credentials are configured.** Make sure `.env` is filled in with your
   `ONEDRIVE_EMAIL` / `ONEDRIVE_PASSWORD`.

3. **A test task folder exists on OneDrive.** Out of the box the smoke tests
   look for a folder named `MO13 Round 1 - Sec 1 - MCQ` under
   `My files/main_tasks/modeloff/`. To match this layout exactly:

   - Open OneDrive in your browser.
   - Under **My files**, create the folder chain `main_tasks/modeloff/MO13 Round 1 - Sec 1 - MCQ/Task/`.
   - Leave that `Task/` folder empty (the smoke tests use `skip_file_upload: true`
     and create the workbook themselves).

   Alternatively, edit `task_configs/smoke_test_tasks.yaml` to point at any
   OneDrive folder you already have — set `task_source` to the parent folder
   name and `task_name` to the leaf folder name.

## How to Run

```bash
# From project root:

# Individual agents
./tests/smoke_tests/run_smoke_claude.sh
./tests/smoke_tests/run_smoke_chatgpt.sh
./tests/smoke_tests/run_smoke_tabai.sh

# All three sequentially
./tests/smoke_tests/run_all_smoke.sh
```

## What Each Test Does

1. Navigates to OneDrive → the test task folder
2. Creates a fresh workbook
3. Opens the AI add-in panel
4. Sends 3 trivial prompts:
   - Create `test_output` sheet with "test" in A1
   - Create `model_smoke` sheet with "smoke test passed" in A1
   - Create `answers_smoke` sheet with "done" in A1
5. Downloads and validates the workbook (file exists, non-empty, openpyxl can open)
6. Saves a JSON completion log

## What to Verify

1. The console exits with `SUCCESS`
2. `json_logs/` contains a JSON file with `"task_status": "success"`
3. The downloaded `.xlsx` exists and is non-empty

## Settings (already wired in the smoke configs)

- 1 agent attempt (no retries on agent failure)
- 2 pipeline attempts (one retry for infra flakes)
- 10 minute total timeout, 2 minutes per prompt
- `skip_file_upload: true` (no local files needed)
