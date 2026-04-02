# Retry Loop Test Suite

End-to-end tests for the dual-counter retry loop in `engine.py`.

## Overview

The retry loop has two counters:
- **`agent_attempts`** — capped by `max_agent_attempts`. Incremented when the agent actually ran.
- **`total_attempts`** — capped by `max_total_attempts`. Incremented on every failure (pipeline + agent).

Agent failures increment both counters; pipeline failures only increment `total_attempts`.

All tests use **claude_excel_agent**.

## How to Run

```bash
# From project root, or from this directory:
./tests/retry_tests/run_test_success.sh
./tests/retry_tests/run_test_missing_sheets.sh
./tests/retry_tests/run_test_timeout.sh
./tests/retry_tests/run_test_pipeline_fail.sh
```

## Test Scenarios

| # | Script | Trigger | Failure Type | Agent Attempts? | JSON? | Retries |
|---|--------|---------|-------------|----------------|-------|---------|
| 1 | `run_test_success.sh` | Correct prompts | None | Yes (1) | 1 file | 0 |
| 2 | `run_test_missing_sheets.sh` | Wrong sheet names | Agent | Yes (2) | 2 files (1 deprecated) | 1 |
| 3 | `run_test_timeout.sh` | 30s timeout | Agent | 0-1 | Maybe | 0 |
| 4 | `run_test_pipeline_fail.sh` | Fake task names | Pipeline | No | No | 3 |

## What to Verify After Each Test

1. **Logs** (`claude_logs/`): Retry messages, status classifications, attempt counter values
2. **JSON files** (`json_logs/`): `task_status`, `agent_failed`, `deprecated`, `attempt_number`
3. **Console output**: Batch runner success/failure per task

## File Structure

```
tasks_configs/
  retry_test_tasks.yaml          # 2 real ModelOff tasks (tests 1-3)
  retry_test_fake_tasks.yaml     # 2 fake task names (test 4)
  template_retry_success.yaml    # Creates model + answers sheets
  template_retry_missing.yaml    # Creates wrong sheet name
  template_retry_timeout.yaml    # 30s timeout (too short)
  template_retry_pipeline.yaml   # Used with fake tasks

runner_configs/
  retry_success_runner.yaml
  retry_missing_runner.yaml
  retry_timeout_runner.yaml
  retry_pipeline_runner.yaml

tests/retry_tests/
  run_test_success.sh
  run_test_missing_sheets.sh
  run_test_timeout.sh
  run_test_pipeline_fail.sh
  README.md                      # This file
```
