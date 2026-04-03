# Excel AI Agent Automation System

Automated batch execution of AI agents (TabAI, Claude, ChatGPT) as Excel Online add-ins for financial modeling tasks. The system navigates OneDrive, opens Excel workbooks, interacts with AI agent add-in panels, and downloads completed workbooks with validation.

## Supported Agents

| Agent | Browser | Add-in | `agent_type` |
|-------|---------|--------|-------------|
| TabAI | Firefox (Playwright) | TabAI Excel add-in | `tabai` |
| Claude | Chrome (CDP) | Claude Excel add-in | `claude_excel_agent` |
| ChatGPT | Chrome (CDP) | ChatGPT Excel add-in | `chatgpt_excel_agent` |

## Architecture

This system follows a composable six-layer pipeline. Green components are user-configurable; blue components are stable framework internals.

![Architecture Diagram](docs/architecture_diagram.png)

**Layers:**

| Layer | Role | Key files |
|-------|------|-----------|
| **Input** | Task definitions, prompt templates, agent parameters | `tasks_configs/templates/*.yaml`, `tasks_configs/examples/*.yaml` |
| **Orchestration** | Batch retry logic, subprocess isolation | `batch_automation_runner.py` |
| **Engine** | Single-task pipeline (setup -> navigate -> AI -> download) | `excel_agent/engine.py` |
| **Navigation** | OneDrive folder traversal OR direct URL (skip OneDrive) | `excel_agent/core/navigation.py`, task config `direct_url` |
| **AI Interaction** | Claude, ChatGPT, TabAI, or your custom agent | `excel_agent/core/*_core.py` |
| **Output** | Downloaded Excel files, validation, JSON logs | `excel_agent/core/file_organizer.py`, `completion_logger.py` |

> See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture guide.

---

## Task Configuration

Configuration is split into two files:

| File | Purpose | You edit per... |
|------|---------|-----------------|
| **Tasks YAML** (`tasks_configs/examples/`) | Where to go, what files to use, what to name output | Each task / project |
| **Template YAML** (`tasks_configs/templates/`) | Which agent, what prompts, retry/timeout settings | Each agent type |

### CLOUD vs LOCAL — Read This First

```
 CLOUD (OneDrive)                          LOCAL (your machine)
 ─────────────────                          ────────────────────
 onedrive_path / direct_url                 upload_files
 = where the BROWSER navigates              = files from YOUR DISK uploaded
   on OneDrive to find/create                 into the add-in chat panel
   the workbook                               as attachments

 template_file                              local_files_base
 = which workbook to OPEN                   = directory for resolving
   in that OneDrive folder                    upload_files paths

 These are COMPLETELY INDEPENDENT.
 Cloud paths and local file paths do NOT need to match.
```

### Tasks YAML Format

```yaml
tasks:
  - task_name: "Q1_Revenue_Analysis"

    # ── CLOUD: Where to go on OneDrive ──────────────────────
    # Option A: Step-by-step folder path
    onedrive_path:
      - "My files"
      - "ProjectX"
      - "analyses"
      - "Q1_Revenue_Analysis"

    # Option B: Direct URL (overrides onedrive_path)
    # direct_url: "https://onedrive.live.com/edit.aspx?..."

    # Which workbook to open in the folder above.
    # Omit or set to "blank" to create a new empty workbook.
    template_file: "Q1_Template.xlsx"

    # ── LOCAL: Files to upload into the AI panel ────────────
    # Paths relative to local_files_base (or CWD).
    # NOT placed on OneDrive — sent as add-in attachments.
    upload_files:
      - "problem_statements/q1_revenue.pdf"
      - "data/quarterly_data.csv"

    # ── OUTPUT: Solution file name ──────────────────────────
    # Final: {YYYYMMDD}_{HHMMSS}_{solution_name}_{agent}_{N}.xlsx
    # Omit to use default naming: {task_name}_Solution_{source}_{agent}_Model.
    solution_name: "Q1_Revenue_Solution"
```

**Navigation priority:** `direct_url` > `onedrive_path` > legacy path construction.

### Template YAML Format

Templates live in `tasks_configs/templates/`. The `prompts` list is what gets sent to the AI — **replace these with your own instructions** for your use case. The default prompts are financial modeling prompts; delete them and write whatever you need.

```yaml
# tasks_configs/templates/claude.yaml
template:
  agent_type: "claude_excel_agent"

  # Base directory for resolving upload_files paths.
  # If omitted, resolved from current working directory.
  # local_files_base: "project_data/"

  prompts:
    - "Analyze the attached dataset and summarize key findings."
    - "Build a model on a new sheet called 'model_main'."
    - "Create an 'answers' sheet with your conclusions."

  retry:
    max_agent_attempts: 3
    max_pipeline_attempts: 10
    timeout_per_task_seconds: 7200

  claude_excel_agent:
    model: opus_4_6
    # ... browser, logging, runtime settings
```

### Model Selection

**Claude Excel add-in:**
```yaml
claude_excel_agent:
  model: opus_4_6  # Options: opus_4_6, sonnet_4_6 (null = current default)
```

**ChatGPT Excel add-in:**
```yaml
chatgpt_excel_agent:
  model: heavy  # Options: fast, standard, heavy (default: heavy)
```

---

## Prerequisites

### Required software

- **Python 3.10+** (3.12 recommended)
- **[uv](https://docs.astral.sh/uv/)** package manager

### Browsers

- **Firefox** — required for the TabAI agent. Playwright manages the browser instance.
- **Google Chrome** (or Chrome Canary) — required for Claude and ChatGPT agents. The automation connects via Chrome DevTools Protocol (CDP).
  - Chrome Canary v148+ has a CDP compatibility issue with Playwright — use **regular Chrome** if you encounter `setDownloadBehavior` errors.

### Playwright browser binaries

```bash
uv run playwright install
# On Linux, you may also need system dependencies:
uv run playwright install-deps
```

### Microsoft accounts & services

- **Microsoft OneDrive account** — task files must be accessible via OneDrive
- **Excel Online access** — requires a Microsoft 365 subscription
- **AI add-in installed in Excel Online** — the appropriate add-in must be installed:
  - **TabAI**: "TabAI" from the Office Store
  - **Claude**: "Claude by Anthropic"
  - **ChatGPT**: "ChatGPT"

---

## Installation

```bash
git clone <repo-url>
cd excel-agents
uv sync
uv run playwright install
```

## Quick Start

### 1. Set up credentials

```bash
cp .env.example .env
# Edit .env with your OneDrive credentials
```

### 2. Set up browser authentication

```bash
# Firefox (TabAI)
./scripts/setup_firefox.sh

# Chrome (Claude / ChatGPT)
./scripts/setup_chrome.sh
```

### 3. Create a task list

Copy `tasks_configs/examples/sample_tasks.yaml` and edit it:

```yaml
tasks:
  - task_name: "My_Analysis"
    onedrive_path:
      - "My files"
      - "my_project"
      - "tasks"
      - "My_Analysis"
    template_file: "blank"
    upload_files:
      - "problem_statement.pdf"
    solution_name: "My_Analysis_Solution"
```

### 4. Run

```bash
# Single agent run
uv run python batch_automation_runner.py \
  --tasks tasks_configs/my_tasks.yaml \
  --runner-config runner_configs/claude.yaml

# Dry run (preview without executing)
uv run python batch_automation_runner.py \
  --tasks tasks_configs/my_tasks.yaml \
  --runner-config runner_configs/claude.yaml \
  --dry-run
```

---

## Direct URL Navigation

If you have a direct link to a task folder, skip folder navigation entirely:

```yaml
tasks:
  - task_name: "My_Task"
    direct_url: "https://onedrive.live.com/?id=YOUR_FOLDER_ID&cid=YOUR_CID"
    template_file: "blank"
    upload_files:
      - "case_study.pdf"
```

The URL should point to the OneDrive folder containing the task files.

---

## Retry Pipeline

Each task runs in a retry loop with two independent counters:

```yaml
retry:
  max_agent_attempts: 3           # Retries where the AI agent ran but failed
  max_pipeline_attempts: 10       # Hard cap including infrastructure failures
  timeout_per_task_seconds: 7200  # Wall-clock timeout per task (seconds)
```

### Task Statuses

| Status | Type | Meaning |
|--------|------|---------|
| `SUCCESS` | Agent | Task completed, Excel validated |
| `TIMEOUT` | Agent | Agent ran but exceeded time limit |
| `PROMPT_FAILED` | Agent | Agent ran but couldn't execute prompts |
| `MISSING_SHEETS` | Agent | Excel missing required "model"/"answers" sheets |
| `DOWNLOAD_FAILED` | Pipeline | Download process failed |
| `FILE_CORRUPTED` | Pipeline | Downloaded file invalid |
| `NAV_FAILED` | Pipeline | Navigation to task failed |
| `EXCEL_FAILED` | Pipeline | Excel Online UI issue |
| `PANEL_FAILED` | Pipeline | Agent panel failed to load |

### Validation

After download, each Excel file is validated:
1. File exists and size > 0
2. `openpyxl` can open it (not corrupted)
3. Contains a sheet with "model" in the name
4. Contains a sheet with "answers" in the name

---

## CLI Options

| Flag | Description |
|------|-------------|
| `--tasks PATH` | Path to task list YAML (required) |
| `--runner-config PATH` | Path to runner config YAML (required) |
| `--dry-run` | Preview tasks without executing |
| `--start-from N` | Skip to the Nth task (0-indexed) |
| `--stop-on-error` | Exit on first failure |
| `--max-sec-per-task N` | Override timeout per task (0 = no limit) |
| `--keep-temp-configs` | Preserve generated per-task config files |

## Output

Each task produces:
- **Excel workbook** — downloaded to `{date}_{agentLabel}/solutions/`
- **JSON completion log** — in `{date}_{agentLabel}/json_logs/`

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

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ONEDRIVE_EMAIL` | Yes | OneDrive login email |
| `ONEDRIVE_PASSWORD` | Yes | OneDrive login password |
| `TABAI_EMAIL` | No | TabAI-specific email (falls back to ONEDRIVE_EMAIL) |
| `TABAI_PASSWORD` | No | TabAI-specific password |

## Troubleshooting

**Authentication expired**: Re-run `./scripts/setup_firefox.sh` or `./scripts/setup_chrome.sh`.

**Agent panel won't load**: Check that the AI add-in is installed in your Excel Online account.

**Chrome CDP connection failed**: Make sure no other Chrome instances are using port 9222. Kill existing Chrome processes and retry.

**Timeout on all tasks**: Increase `timeout_per_task_seconds` in your template config.

**Playwright not installed**: Run `uv run playwright install`.

**Validate config before running**:
```bash
uv run python batch_automation_runner.py --dry-run \
  --tasks tasks_configs/my_tasks.yaml \
  --runner-config runner_configs/claude.yaml
```

---

## Legacy Task Format (v1)

If you are migrating from the original BizbenchV1 format, the old `task_source` approach still works:

```yaml
task_source: "modeloff"
tasks:
  - task_name: "MO13 Round 1 - Sec 1 - MCQ"
  - task_name: "MO14 Round 1 - Sec 1 - MCQ"
```

This builds the path from `file_path` (template) + source mapping + task_name + `"Task"`:
- `fmwc` -> `fmwc/`
- `modeloff` -> `modeloff/`
- `wallstreetprep` -> `wsp/`

File upload is automatic: everything in the local `Task/` folder except the workbook gets uploaded. See `tasks_configs/examples/legacy_tasks.yaml`.

---

## Directory Structure

```
excel-agents/
├── excel_agent/
│   ├── engine.py                 # Main single-task entry point
│   ├── firefox_browser.py        # Firefox session manager
│   ├── chrome_browser.py         # Chrome CDP session manager
│   ├── pdf_upload.py             # PDF upload helper
│   └── core/
│       ├── ai_agent_base.py      # Base agent class (shared logic)
│       ├── tabai_core.py         # TabAI agent implementation
│       ├── claude_core.py        # Claude agent implementation
│       ├── chatgpt_core.py       # ChatGPT agent implementation
│       ├── browser_manager.py    # Browser lifecycle management
│       ├── navigation.py         # OneDrive folder navigation
│       ├── excel_operations.py   # Excel Online UI interactions
│       ├── file_manager.py       # File discovery & workbook detection
│       ├── file_organizer.py     # Download, validation, TaskStatus
│       ├── auth_handler.py       # Authentication logic
│       ├── config_loader.py      # YAML config parsing + retry settings
│       ├── completion_logger.py  # JSON completion logging
│       └── logging_setup.py      # Log configuration
├── batch_automation_runner.py    # Batch orchestrator (retry loop)
├── runner_configs/               # Agent runner configs
├── tasks_configs/
│   ├── templates/                # Agent template configs
│   └── examples/                 # Example task lists
├── scripts/                      # Browser auth setup scripts
├── tests/                        # Retry pipeline tests
├── .env.example                  # Credential template
└── pyproject.toml                # Dependencies
```
