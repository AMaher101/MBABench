# Excel AI Agent Automation System

Automated batch execution of AI agents that work *inside Excel Online* (TabAI, Claude, and ChatGPT add-ins). The system signs into your Microsoft 365 account, navigates OneDrive, opens an Excel workbook, drives the AI add-in panel through one or more prompts, and downloads + validates the resulting workbook.

> **Looking at the BizbenchV1 repo as a whole?** See [`../AGENTS.md`](../AGENTS.md) for an orientation across all agent suites in this repo.

---

## How this compares to `gui-agents-master`

The sibling repo, [`gui-agents-master/`](../gui-agents-master/), runs AI agents inside the *web chat UIs* (claude.ai, chatgpt.com) and downloads any Excel artifacts the AI produces. Same kind of benchmark output, very different runtime.

|  | This repo (`excel-agents-master`) | Sibling (`gui-agents-master`) |
|---|---|---|
| **Where the AI runs** | Excel Online add-in panel | Web chat UI (claude.ai, chatgpt.com) |
| **Required account** | Microsoft 365 + OneDrive | Provider login (Claude.ai or ChatGPT subscription) |
| **Browsers** | Chrome Canary (Claude/ChatGPT) + Firefox (TabAI) | Chrome |
| **Cloud orchestration** | None — runs only on your local machine | Full EC2 dispatcher in `infra/` for multi-box scaling |

→ See [`../AGENTS.md`](../AGENTS.md) for the full feature matrix and the "which suite should I pick?" guide.

---

## Two ways to run

There are two runners in this repo. **External users should always use the local runner**; the DB-driven runner is for the internal BizbenchV1 team.

| Runner | Audience | Tasks come from | Attempts go to | Retry behavior |
|---|---|---|---|---|
| **`batch_automation_runner.py`** | **Default — everyone** | Local YAML files (`tasks_configs/*.yaml`) | Local files only | Dual-counter retry loop (per-agent + per-pipeline) |
| **`infra/run.py`** | **BizbenchV1 internal team only** | Internal Postgres + S3, *or* a local YAML run-config | Either local NDJSON, or upload to internal S3 + insert into Postgres | Single attempt per task — rerun manually for another |

Both runners execute on **your local machine**. There is no cloud orchestration on the Excel side — running TabAI / OneDrive Excel Online inside an EC2 box is impractical, so scale-out lives only in the gui-agents repo.

If you're outside the BizbenchV1 team and want to replicate the DB-driven path against your own Postgres + S3, see the [BYO database](#byo-database-external-users) note at the end of the infra section.

---

## Prerequisites

- **Python 3.10+** (3.12 recommended)
- **[uv](https://docs.astral.sh/uv/)** package manager
- **Google Chrome Canary** — required for the Claude and ChatGPT agents. The setup script launches Canary on port 9222; the runtime connects to that same port via Chrome DevTools Protocol. (If Canary v148+ gives you `setDownloadBehavior` errors, see [Troubleshooting](#troubleshooting).)
- **Firefox** — required for the TabAI agent. Playwright manages this for you.
- **Microsoft 365 account** with OneDrive access (an Excel Online subscription).
- **The relevant Excel add-in installed** in your Microsoft 365 account: TabAI (from the Office Store), Claude (Claude by Anthropic), or ChatGPT.

### Supported agents

| Agent | Browser | Add-in | `agent_type` value |
|---|---|---|---|
| TabAI | Firefox | TabAI | `tabai` |
| Claude | Chrome Canary (CDP) | Claude by Anthropic | `claude_excel_agent` |
| ChatGPT | Chrome Canary (CDP) | ChatGPT | `chatgpt_excel_agent` |

---

## Install

```bash
git clone <repo-url>
cd excel-agents-master
uv sync
uv run playwright install
# On Linux only, you may also need: uv run playwright install-deps
```

---

## Quickstart — local YAML path (default)

This is the path everyone should start with. You provide a YAML task list, the runner drives Excel Online for each task, and Excel files land on disk under a dated output folder.

### 1. Set Microsoft 365 credentials

```bash
cp .env.example .env
# Edit .env and set ONEDRIVE_EMAIL and ONEDRIVE_PASSWORD
```

| Variable | Required | Purpose |
|---|---|---|
| `ONEDRIVE_EMAIL` | Yes | Microsoft 365 login email |
| `ONEDRIVE_PASSWORD` | Yes | Microsoft 365 login password |
| `TABAI_EMAIL` | No | TabAI-specific login (falls back to `ONEDRIVE_EMAIL`) |
| `TABAI_PASSWORD` | No | TabAI-specific password |

### 2. One-time browser session setup

These scripts open an interactive browser window where you complete the Microsoft 365 sign-in (handling 2FA / MFA prompts). The session is then persisted to a local profile directory, so subsequent automated runs reuse the login until cookies expire (typically a few weeks).

```bash
# For Claude / ChatGPT (Chrome Canary on port 9222, profile at ~/.chrome-canary-automation)
./scripts/setup_chrome.sh

# For TabAI (Firefox)
./scripts/setup_firefox.sh
```

Run the setup for whichever agent(s) you plan to use. You'll know it worked when you can navigate to OneDrive in the launched browser without being asked to sign in again.

### 3. Write a task list

Copy `tasks_configs/examples/sample_tasks.yaml` and edit. The minimum is a name, where on OneDrive the task lives, and any local files to attach into the AI panel:

```yaml
tasks:
  - task_name: "My_Analysis"
    onedrive_path:
      - "My files"
      - "my_project"
      - "tasks"
      - "My_Analysis"
    template_file: "blank"           # or the name of an .xlsx in that folder
    upload_files:
      - "problem_statement.pdf"      # path on YOUR disk, attached to the AI panel
    solution_name: "My_Analysis_Solution"
```

**Cloud vs local paths are independent.** `onedrive_path` is *where the browser navigates on OneDrive*; `upload_files` is *files from your disk that get attached to the AI chat panel*. They don't have to mirror each other.

### 4. Run

```bash
# Dry-run first — parses configs, resolves task paths, prints the resolved
# engine_config for each task. Does NOT open a browser or send prompts.
uv run python batch_automation_runner.py \
  --tasks tasks_configs/my_tasks.yaml \
  --runner-config runner_configs/claude.yaml \
  --dry-run

# For real
uv run python batch_automation_runner.py \
  --tasks tasks_configs/my_tasks.yaml \
  --runner-config runner_configs/claude.yaml
```

Output lands under `{YYYYMMDD}_{agent_label}/` in the directory you ran from — `solutions/` (downloaded Excel files) and `json_logs/` (per-task completion records).

---

## Quickstart — DB-driven path (`infra/run.py`)

> **For the BizbenchV1 internal team.** This path requires credentials for our private Postgres database and our `bizbench` S3 bucket. **External users:** see the [BYO database](#byo-database-external-users) note below, or stick with the local YAML path above.

`infra/run.py` is a separate runner that pulls tasks from the BizbenchV1 Postgres `tasks` table, downloads any required files from S3, runs the agent locally, then uploads the resulting workbook to S3 and inserts a row into `task_attempts`. It coexists with `batch_automation_runner.py` — neither replaces the other.

### Layout

```
infra/
├── run.py                              # CLI entry point: python -m infra.run
└── configs/
    ├── loader.py                       # Hierarchical YAML merge
    ├── agent_identity.py               # provider.kind → AgentIdentity
    ├── configs.default.yaml            # Full schema. Don't edit; override below.
    ├── configs.yaml                    # Gitignored — your machine-specific overrides.
    └── run_configs/
        ├── bizbench_run_examples/      # DB-driven examples (one per agent)
        └── local_run_examples/         # Task-shaped YAML examples
task_io/
├── base.py                             # TaskSpec / AttemptResult protocols
├── registry.py                         # build_source(cfg) / build_sink(cfg)
├── sources/{yaml_source,postgres_s3}.py
└── sinks/{local_sink,postgres_s3}.py
```

### Config hierarchy (later wins)

1. `infra/configs/configs.default.yaml` — checked-in defaults, full schema
2. `infra/configs/configs.yaml` — **gitignored**, machine-specific (DB url, AWS creds)
3. `--run-config <path>` — run-scoped overlay (which tasks, which provider)

A `--run-config` file can be either *overlay-shaped* (no top-level `task_name`) or *task-shaped* (top-level `task_name` / `tasks`). Task-shaped files force `source.kind: yaml` and are loaded via `YamlTaskSource`.

### One-time setup

1. `uv sync` (the `infra/run.py` path adds `boto3` and `psycopg2-binary` to the lock).
2. Create `infra/configs/configs.yaml` (gitignored) with your DB url + AWS creds:
   ```yaml
   database:
     url: "postgresql://.../BizbenchV1?sslmode=require&channel_binding=require"
   aws:
     access_key_id: "AKIA..."
     secret_access_key: "..."
   ```
   Or leave the values empty and rely on the corresponding `*_env` keys (`BIZBENCHJUDGE_KEYS_DATABASE_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) in your environment.
3. Same Chrome-CDP / Microsoft-365 prereqs as the local quickstart above — `scripts/setup_chrome.sh` once; OneDrive session persists in the Chrome profile.

### Run

```bash
# Pull eligible tasks from the DB, run each via Claude Excel, write attempts to
# local NDJSON (no DB writes).
uv run python -m infra.run \
  --run-config infra/configs/run_configs/bizbench_run_examples/sample_bizbench_claude_excel.yaml

# Same, but upload solutions to S3 and insert task_attempts rows.
uv run python -m infra.run \
  --run-config infra/configs/run_configs/bizbench_run_examples/sample_bizbench_write.yaml

# Run a one-off local task (task-shaped run-config). Bring your own data
# files; the shipped sample_task.yaml is a structural example and references
# files under data/sample/ that aren't included in the repo.
uv run python -m infra.run \
  --run-config infra/configs/run_configs/local_run_examples/sample_task.yaml

# Dry-run — prints the merged engine_config per task, runs preflight
# validation, no browser or DB writes.
uv run python -m infra.run --dry-run \
  --run-config infra/configs/run_configs/bizbench_run_examples/sample_bizbench_claude_excel.yaml

# Run exactly one DB task by id (overrides filters; ignores skip_already_attempted).
uv run python -m infra.run --task-id 42 \
  --run-config infra/configs/run_configs/bizbench_run_examples/sample_bizbench_write.yaml
```

### How attempts are labeled in the DB

`task_attempts.agent_model_name`, `agent_folder`, and `agent_model_type` are **derived** from `provider.kind` via `infra/configs/agent_identity.py` — they are not yaml fields you set per-run.

| `provider.kind` | `agent_model_name` | `agent_folder` (S3) | `agent_model_type` |
|---|---|---|---|
| `claude_excel_agent` | `claude_excel_agent` | `claude_excel_agent` | `gui` |
| `chatgpt_excel_agent` | `chatgpt_excel_agent` | `chatgpt_excel_agent` | `gui` |
| `tabai` | `tabai` | `tabai` | `gui` |

`gui` matches the convention used by every existing browser-based attempt in `task_attempts`. To bifurcate by model (Opus 4.6 vs. Sonnet 4.6, etc.) later, extend the identity tables in `agent_identity.py` to key on `(model,)` and backfill historical rows in a separate migration.

### BYO database (external users)

If you want to run the DB-driven path against your own infrastructure rather than ours, the table shape is defined by `task_io/sources/postgres_s3.py` (read columns) and `task_io/sinks/postgres_s3.py` (write columns); the S3 layout is `s3://<bucket>/<task_path>` with attempts written under the per-agent folder. You'd need to provision your own Postgres + S3 bucket and point `infra/configs/configs.yaml` at them. We don't ship a schema migration for external use — the local YAML quickstart is the supported turnkey path for outside use.

---

## Configuration reference

### Tasks YAML format

```yaml
tasks:
  - task_name: "Q1_Revenue_Analysis"

    # Where on OneDrive the browser should navigate.
    onedrive_path:
      - "My files"
      - "ProjectX"
      - "Q1_Revenue_Analysis"

    # OR a direct OneDrive URL (overrides onedrive_path):
    # direct_url: "https://onedrive.live.com/edit.aspx?..."

    # Which workbook to open in that folder. "blank" = create a new empty one.
    template_file: "Q1_Template.xlsx"

    # Local files attached to the AI add-in panel. Paths resolve against
    # local_files_base from the template (or CWD if not set).
    upload_files:
      - "problem_statements/q1_revenue.pdf"

    # Output filename: {YYYYMMDD}_{HHMMSS}_{solution_name}_{agent}_{N}.xlsx
    solution_name: "Q1_Revenue_Solution"
```

**Navigation priority:** `direct_url` > `onedrive_path` > task-source shorthand (see below).

### Template YAML format

Templates live in `tasks_configs/templates/` and pin the agent + prompts + retry/timeout behavior:

```yaml
template:
  agent_type: "claude_excel_agent"

  # Base directory for resolving upload_files (defaults to CWD if omitted).
  # local_files_base: "project_data/"

  prompts:
    - "Analyze the attached dataset and summarize key findings."
    - "Build a model on a new sheet called 'model_main'."
    - "Create an 'answers' sheet with your conclusions."

  retry:
    max_agent_attempts: 3            # Retries where the AI ran but failed
    max_pipeline_attempts: 10        # Hard cap including infra failures
    timeout_per_task_seconds: 7200

  claude_excel_agent:
    model: opus_4_6                  # opus_4_6 | sonnet_4_6
    # skip_file_upload: false        # Useful for prompt-only smoke tests
```

### Model selection

| Agent | Field | Options |
|---|---|---|
| Claude | `claude_excel_agent.model` | `opus_4_6`, `sonnet_4_6` |
| ChatGPT | `chatgpt_excel_agent.model` | `fast`, `standard`, `heavy` (default `heavy`) |
| TabAI | `tabai.model` | template-defined |

### Task-source shorthand

If all your tasks live under a shared parent folder on OneDrive *and* on disk, you can use a single `task_source` key plus per-task `task_name` to derive paths:

```yaml
task_source: "modeloff"
tasks:
  - task_name: "Round 1 - Section 1 - MCQ"
  - task_name: "Round 1 - Section 2 - MCQ"
```

The OneDrive path is built as `file_path` (from the template) + `task_source` + `task_name` + `"Task"`; local files are read from `main_tasks/{task_source}/{task_name}/Task/`. Everything in that local `Task/` folder except the workbook gets uploaded automatically. See `tasks_configs/examples/task_source_format.yaml` for a complete example.

### Direct URL navigation

If you have a direct OneDrive link to a task folder, skip folder navigation entirely:

```yaml
tasks:
  - task_name: "My_Task"
    direct_url: "https://onedrive.live.com/?id=YOUR_FOLDER_ID&cid=YOUR_CID"
    template_file: "blank"
    upload_files:
      - "case_study.pdf"
```

---

## CLI options (`batch_automation_runner.py`)

| Flag | Description |
|---|---|
| `--tasks PATH` | Path to task list YAML (required) |
| `--runner-config PATH` | Path to runner config YAML (required) |
| `--dry-run` | Parse + resolve, do not open a browser or run prompts |
| `--start-from N` | Skip to the Nth task (0-indexed) |
| `--stop-on-error` | Exit on first failure |
| `--max-sec-per-task N` | Override timeout per task (0 = no limit) |
| `--keep-temp-configs` | Preserve generated per-task config files |

---

## Output structure

Each task produces:

- **Excel workbook** — downloaded to `{YYYYMMDD}_{agent_label}/solutions/`
- **JSON completion log** — written to `{YYYYMMDD}_{agent_label}/json_logs/`

Both are created in the directory you ran the runner from. JSON log shape:

```json
{
  "session_start": "2026-03-19T10:00:00",
  "agent_name": "claude_excel_agent",
  "tasks": [{
    "task_name": "My_DCF_Model",
    "attempt_number": 1,
    "task_status": "success",
    "duration_seconds": 120.5,
    "prompts": [{"prompt_text": "...", "success": true, "duration_seconds": 45.0}]
  }]
}
```

### Retry pipeline & task statuses

Each task runs in a retry loop with two independent counters: `max_agent_attempts` (the AI ran but failed) and `max_pipeline_attempts` (a hard cap including infrastructure failures), plus a `timeout_per_task_seconds` wall-clock limit.

| Status | Type | Meaning |
|---|---|---|
| `SUCCESS` | Agent | Task completed, Excel validated |
| `TIMEOUT` | Agent | Agent ran but exceeded time limit |
| `PROMPT_FAILED` | Agent | Agent ran but couldn't execute prompts |
| `DOWNLOAD_FAILED` | Pipeline | Download process failed |
| `FILE_CORRUPTED` | Pipeline | Downloaded file invalid (openpyxl can't open) |
| `NAV_FAILED` | Pipeline | Navigation to task failed |
| `EXCEL_FAILED` | Pipeline | Excel Online UI issue |
| `PANEL_FAILED` | Pipeline | Agent panel failed to load |

After download, each Excel file is validated: file exists, size > 0, `openpyxl` can open it.

---

## Troubleshooting

**Authentication expired.** Re-run `./scripts/setup_chrome.sh` or `./scripts/setup_firefox.sh` and complete the sign-in again.

**Agent panel won't load.** Verify the relevant AI add-in is installed in your Microsoft 365 account.

**`Chrome not reachable on CDP port 9222` immediately after a fresh `setup_chrome.sh`.** This is almost always a setup-vs-runtime mismatch, not an expired session. Check that `excel_agent/chrome_browser.py` and `excel_agent/core/browser_manager.py` agree on `CDP_PORT`, the profile dir, and the Chrome binary they look for. If you've edited either file, both must change together. (`tests/infra/test_infra_smoke.py` has a parity test that locks these two files together — run it after any browser-config edit.)

**`setDownloadBehavior` errors with Chrome Canary v148+.** A known Canary/Playwright incompatibility. The fix is to point both `chrome_browser.py` and `browser_manager.py` at regular Chrome instead of Canary (binary path + profile dir) — keep them in sync.

**No other Chrome process should be on port 9222.** Kill any stray Chrome instances (`pkill -f Chrome` on macOS / Linux) before retrying.

**Timeouts on every task.** Increase `retry.timeout_per_task_seconds` in your template config. The defaults are 7200s (2h) for Claude / ChatGPT and 5400s (90min) for TabAI.

**Playwright not installed.** `uv run playwright install`.

**Sample DB run-config preflight fails on missing files.** `infra/configs/run_configs/local_run_examples/sample_task.yaml` is a *structural* example that references files under `data/sample/` not included in the repo. Replace the `upload_files` paths with files that exist on your disk before running it.

**The `run_missing_v8_*.sh` scripts.** These are sample drivers for specific (agent, task source) combinations from past benchmark runs. They are examples, not required steps — you do not need to run them.

**Validate config before running.**
```bash
uv run python batch_automation_runner.py --dry-run \
  --tasks tasks_configs/my_tasks.yaml \
  --runner-config runner_configs/claude.yaml
```

### Smoke tests

`tests/smoke_tests/` contains short end-to-end checks that drive each agent through a trivial real run. They require a small amount of OneDrive setup before they can run — see [`tests/smoke_tests/README.md`](tests/smoke_tests/README.md) for the exact folder layout and step-by-step instructions.

---

## Architecture

The system follows a composable six-layer pipeline. Green components are user-configurable; blue components are stable framework internals.

![Architecture Diagram](docs/architecture_diagram.png)

| Layer | Role | Key files |
|---|---|---|
| **Input** | Task definitions, prompt templates, agent parameters | `tasks_configs/templates/*.yaml`, `tasks_configs/examples/*.yaml` |
| **Orchestration** | Batch retry logic, subprocess isolation | `batch_automation_runner.py` |
| **Engine** | Single-task pipeline (setup → navigate → AI → download) | `excel_agent/engine.py` |
| **Navigation** | OneDrive folder traversal OR direct URL | `excel_agent/core/navigation.py` |
| **AI Interaction** | Claude, ChatGPT, TabAI, or your custom agent | `excel_agent/core/*_core.py` |
| **Output** | Downloaded Excel files, validation, JSON logs | `excel_agent/core/file_organizer.py`, `completion_logger.py` |

> See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full architecture guide and instructions on adding your own agent.

---

## Directory structure

```
excel-agents-master/
├── batch_automation_runner.py    # Local YAML batch orchestrator (default runner)
├── infra/                        # DB-driven runner (BizbenchV1 internal team)
│   ├── run.py
│   └── configs/
├── task_io/                      # Pluggable task-source / attempt-sink layer
├── excel_agent/
│   ├── engine.py                 # Per-task pipeline entry point
│   ├── chrome_browser.py         # Chrome CDP setup-time launcher
│   ├── firefox_browser.py        # Firefox setup-time launcher
│   └── core/
│       ├── ai_agent_base.py      # Base agent class
│       ├── tabai_core.py         # TabAI agent implementation
│       ├── claude_core.py        # Claude agent implementation
│       ├── chatgpt_core.py       # ChatGPT agent implementation
│       ├── browser_manager.py    # Runtime Chrome launcher (must match chrome_browser.py)
│       ├── navigation.py         # OneDrive folder navigation
│       ├── excel_operations.py   # Excel Online UI interactions
│       ├── file_manager.py       # File discovery & workbook detection
│       ├── file_organizer.py     # Download, validation, TaskStatus
│       ├── auth_handler.py       # Authentication logic
│       ├── config_loader.py      # YAML config parsing
│       └── completion_logger.py  # JSON completion logging
├── runner_configs/               # Per-agent runner configs
├── tasks_configs/
│   ├── templates/                # Agent template configs
│   └── examples/                 # Example task lists
├── scripts/                      # setup_chrome.sh / setup_firefox.sh
├── tests/                        # Retry pipeline + smoke tests
├── docs/                         # Architecture diagram + ARCHITECTURE.md
├── .env.example                  # Credential template
└── pyproject.toml
```
