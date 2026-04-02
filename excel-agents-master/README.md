# Excel AI Agent Automation System

Automated batch execution of AI agents (TabAI, Claude, ChatGPT) as Excel Online add-ins for financial modeling tasks. The system navigates OneDrive, opens Excel workbooks, interacts with AI agent add-in panels, and downloads completed workbooks with validation.

## Supported Agents

| Agent | Browser | Add-in | `agent_type` |
|-------|---------|--------|-------------|
| TabAI | Firefox (Playwright) | TabAI Excel add-in | `tabai` |
| Claude | Chrome (CDP) | Claude Excel add-in | `claude_excel_agent` |
| ChatGPT | Chrome (CDP) | ChatGPT Excel add-in | `chatgpt_excel_agent` |

The `agent_type` field in your template config determines which agent implementation is used. Each agent communicates with its respective AI service through an Excel Online add-in panel.

## Architecture

This system follows a composable six-layer pipeline. Green components are user-configurable; blue components are stable framework internals.

![Architecture Diagram](docs/architecture_diagram.png)

**Layers:**

| Layer | Role | Key files |
|-------|------|-----------|
| **Input** | Task definitions, prompt templates, agent parameters | `tasks_configs/templates/*.yaml`, `tasks_configs/examples/*.yaml` |
| **Orchestration** | Batch retry logic, subprocess isolation | `batch_automation_runner.py` |
| **Engine** | Single-task pipeline (setup → navigate → AI → download) | `excel_agent/engine.py` |
| **Navigation** | OneDrive folder traversal OR direct URL (skip OneDrive) | `excel_agent/core/navigation.py`, task config `direct_url` |
| **AI Interaction** | Claude, ChatGPT, TabAI, or your custom agent | `excel_agent/core/*_core.py` |
| **Output** | Downloaded Excel files, validation, JSON logs | `excel_agent/core/file_organizer.py`, `completion_logger.py` |

### Adapting for Your Research

1. **Edit prompt templates** — `tasks_configs/templates/*.yaml` contains the prompt sequence sent to the AI. Replace with your own instructions.
2. **Define your task list** — Create a YAML in `tasks_configs/examples/` listing your tasks.
3. **Choose your model** — Set `model:` in the template (e.g., `opus_4_6`, `sonnet_4_6`, or `fast`/`standard`/`heavy` for ChatGPT).
4. **Use direct URLs** — Set `direct_url` in task configs to skip OneDrive navigation entirely.
5. **Customize validation** — Edit `validate_excel_file()` in `file_organizer.py` to match your expected output schema (e.g., change required sheet names).
6. **Add a new agent** — Extend `AIAgentCore` base class with 4 methods: `_find_agent_frame()`, `_find_input_field()`, `_send_prompt()`, `_wait_for_response()`.

> See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture guide.

## Prerequisites

### Required software

- **Python 3.10+** (3.12 recommended)
- **[uv](https://docs.astral.sh/uv/)** package manager

### Browsers

- **Firefox** — required for the TabAI agent. Playwright manages the browser instance.
- **Google Chrome** (or Chrome Canary) — required for Claude and ChatGPT agents. The automation connects via Chrome DevTools Protocol (CDP).
  - Chrome Canary v148+ has a CDP compatibility issue with Playwright — use **regular Chrome** if you encounter `setDownloadBehavior` errors.

### Playwright browser binaries

Playwright requires browser binaries to be downloaded separately from the Python package:

```bash
# Install all browsers (Firefox + Chromium)
uv run playwright install

# On Linux, you may also need system dependencies:
uv run playwright install-deps
```

### Microsoft accounts & services

- **Microsoft OneDrive account** — task files (problem statements, data files) must be uploaded to OneDrive
- **Excel Online access** — requires a Microsoft 365 subscription (Business, Education, or Personal)
- **AI add-in installed in Excel Online** — the appropriate add-in must be installed in your Excel Online account:
  - **TabAI**: Install the "TabAI" add-in from the Office Store
  - **Claude**: Install the "Claude by Anthropic" add-in
  - **ChatGPT**: Install the "ChatGPT" add-in

### OneDrive folder structure

Task files must be organized in OneDrive following this structure:

```
My files/
└── YOUR_PROJECT_ID/
    └── main_tasks/
        ├── fmwc/
        │   ├── Task_Name_1/
        │   │   └── Task/          ← problem statement PDFs, data files
        │   └── Task_Name_2/
        │       └── Task/
        ├── modeloff/
        │   └── Task_Name_3/
        │       └── Task/
        └── wsp/
            └── Task_Name_4/
                └── Task/
```

The `file_path` in your template config points to the base path (e.g., `["My files", "YOUR_PROJECT_ID", "main_tasks"]`), and `task_source` + `task_name` determine the subfolder.

Alternatively, you can provide a `direct_url` per task to skip folder-by-folder navigation entirely (see [Direct URL Navigation](#direct-url-navigation) below).

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

Browser sessions must be established before running tasks. Re-run when sessions expire.

**Firefox (TabAI):**
```bash
./scripts/setup_firefox.sh
```
A Firefox window opens. Log into OneDrive and TabAI, then press `Ctrl+C` to save the session.

**Chrome (Claude / ChatGPT):**
```bash
./scripts/setup_chrome.sh
```
A Chrome window opens via CDP. Log into OneDrive and your AI agent. The session persists across runs.

### 3. Create a task list

Copy `tasks_configs/examples/sample_tasks.yaml` and edit it with your tasks:

```yaml
tasks:
  - task_name: "My_DCF_Model"
    task_source: "fmwc"
  - task_name: "My_LBO_Analysis"
    task_source: "modeloff"
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

## Configuration

### Runner Configs (`runner_configs/`)

Each runner config specifies which agent template and script to use:

| File | Agent |
|------|-------|
| `tabai.yaml` | TabAI (Firefox) |
| `claude.yaml` | Claude (Chrome) |
| `chatgpt.yaml` | ChatGPT (Chrome) |

### Template Configs (`tasks_configs/templates/`)

Templates define agent-specific settings: browser type, timeouts, prompts, and retry behavior.

| File | Agent | Default Timeout |
|------|-------|----------------|
| `tabai.yaml` | TabAI | 90 min |
| `claude.yaml` | Claude | 2 hours |
| `chatgpt.yaml` | ChatGPT | 2 hours |

Key template fields:
- `agent_type` — selects which agent implementation to use (effectively selects the AI provider)
- `model` — selects which model/reasoning mode to use within the provider (see below)
- `file_path` — OneDrive base path to your task files
- `prompts` — list of prompt strings sent sequentially to the agent
- `retry` — retry and timeout configuration (see below)

### Model Selection

Each agent supports configurable model selection via the `model` field in the agent-specific config section:

**Claude Excel add-in:**
```yaml
claude_excel_agent:
  model: opus_4_6  # Options: opus_4_6, sonnet_4_6 (null = use current default)
```

| Config value | Add-in model |
|---|---|
| `opus_4_6` | Opus 4.6 |
| `sonnet_4_6` | Sonnet 4.6 |
| `null` / omitted | Uses current default |

**ChatGPT Excel add-in:**
```yaml
chatgpt_excel_agent:
  model: heavy  # Options: fast, standard, heavy (default: heavy)
```

| Config value | Reasoning mode |
|---|---|
| `fast` | Fast |
| `standard` | Standard |
| `heavy` | Heavy (default) |

### Task List Format

```yaml
tasks:
  - task_name: "Task_Directory_Name"   # Must match folder name in OneDrive
    task_source: "fmwc"                # fmwc | modeloff | wallstreetprep
    # skip: true                       # Optional: skip this task
    # direct_url: "https://..."        # Optional: direct OneDrive URL (see below)
```

The `task_source` + `task_name` determine the OneDrive path:
```
{file_path} / {source_folder} / {task_name} / Task
```

Where `source_folder` maps: `fmwc` -> `fmwc`, `modeloff` -> `modeloff`, `wallstreetprep` -> `wsp`.

## Direct URL Navigation

By default, the system navigates through OneDrive's folder hierarchy step by step. If you have a direct link to a task folder, you can skip this by providing `direct_url` in your task config:

```yaml
tasks:
  - task_name: "My_Task"
    task_source: "fmwc"
    direct_url: "https://onedrive.live.com/?id=YOUR_FOLDER_ID&cid=YOUR_CID"
```

When `direct_url` is set, the automation navigates directly to that URL instead of clicking through folders. This is useful when:
- Your OneDrive folder structure doesn't match the expected layout
- You want to use SharePoint URLs
- Folder navigation is unreliable due to slow loading

The URL should point to the folder containing the task files (the equivalent of the `Task/` folder).

## Local File Paths

Task files referenced in the `file_path` template config correspond to your OneDrive folder structure. The `file_path` list defines the base path segments that the automation navigates through:

```yaml
# In your template config
file_path:
  - "My files"
  - "YOUR_PROJECT_ID"
  - "main_tasks"
```

Downloaded solution files are saved locally to `{date}_{agentLabel}/{task_source}/{task_name}/`.

## Retry Pipeline

Each task runs in a retry loop with two independent counters. Configure in your template YAML under `retry:`:

```yaml
retry:
  max_agent_attempts: 3           # Retries where the AI agent ran but failed
  max_pipeline_attempts: 10       # Hard cap including infrastructure failures
  timeout_per_task_seconds: 7200  # Wall-clock timeout per task (seconds)
```

**Infrastructure failures** (OneDrive unreachable, Excel won't load, add-in panel timeout) retry automatically without counting toward `max_agent_attempts`. Only failures where the agent actually ran and produced a result (timeout, wrong output, missing sheets) count as agent attempts.

### Task Statuses

After each attempt, the task gets one of these statuses:

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
- **Excel workbook** — downloaded to `{date}_{agentLabel}/{task_source}/{task_name}/`
- **JSON completion log** — in `{date}_{agentLabel}/json_logs/` with timing, status, and prompt details

Completion log format:
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

**Agent panel won't load**: Check that the AI add-in is installed in your Excel Online account. Try opening Excel Online manually first.

**Chrome CDP connection failed**: Make sure no other Chrome instances are using port 9222. Kill existing Chrome processes and retry.

**Timeout on all tasks**: Increase `timeout_per_task_seconds` in your template config, or check your network connection to OneDrive.

**Playwright not installed**: If you see `playwright._impl._errors.Error: Executable doesn't exist`, run:
```bash
uv run playwright install
# On Linux, also install system dependencies:
uv run playwright install-deps
```

**Validate config before running**:
```bash
uv run python batch_automation_runner.py --dry-run \
  --tasks tasks_configs/my_tasks.yaml \
  --runner-config runner_configs/claude.yaml
```

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
│   ├── tabai.yaml
│   ├── claude.yaml
│   └── chatgpt.yaml
├── tasks_configs/
│   ├── templates/                # Agent template configs
│   │   ├── tabai.yaml
│   │   ├── claude.yaml
│   │   └── chatgpt.yaml
│   └── examples/
│       └── sample_tasks.yaml     # Example task list
├── scripts/
│   ├── setup_firefox.sh          # Firefox auth setup
│   └── setup_chrome.sh           # Chrome auth setup
├── tests/                        # Retry pipeline tests
├── .env.example                  # Credential template
└── pyproject.toml                # Dependencies
```
