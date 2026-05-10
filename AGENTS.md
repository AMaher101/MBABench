# BizbenchV1 — Agent Suites

This repo contains three agent suites for running AI on financial-modeling Excel tasks, plus a grading harness. They differ in *how* the AI interacts with Excel — through an Excel add-in, through a web chat UI, or through a headless MCP server. They produce comparable outputs (an Excel file per task) so the same judge can grade attempts from any of them.

## What's in this repo

| Directory | What it does |
|---|---|
| [`excel-agents-master/`](excel-agents-master/) | AI agents that work *inside Excel Online* via OneDrive add-ins (TabAI, Claude, ChatGPT). Drives a real Excel session in a browser. |
| [`gui-agents-master/`](gui-agents-master/) | AI agents that work *inside the web chat UIs* of Claude.ai and ChatGPT. Uploads task files into the chat and downloads the Excel artifacts the model produces. |
| [`cli-agents-master/`](cli-agents-master/) | Headless agent that builds Excel via the OpenAI API + Excel MCP Server. No browser, no Excel UI; LibreOffice handles formula recalc. |
| [`judge/`](judge/) | LLM-based grader for Excel-task attempts produced by any of the agent suites. |

---

## Pick an agent suite

| Dimension | excel-agents | gui-agents | cli-agents |
|---|---|---|---|
| **What the AI drives** | Excel Online add-in panel | Web chat UI (claude.ai, chatgpt.com) | OpenAI API + Excel MCP Server (no UI) |
| **Browser required** | Chrome Canary (Claude/ChatGPT) + Firefox (TabAI) | Regular Chrome | None — runs headless |
| **Account / auth** | Microsoft 365 + OneDrive | Provider login (Claude.ai, ChatGPT Plus/Pro) | OpenAI API key |
| **Agents available** | TabAI, Claude (`claude_excel_agent`), ChatGPT (`chatgpt_excel_agent`) | Claude (`claude_web`), ChatGPT (`chatgpt_web`, Agent + Extended Pro modes) | OpenAI models via the API |
| **Output** | Downloaded Excel + JSON completion log | Downloaded Excel + JSON completion log | Generated Excel + per-iteration logs |
| **Cloud orchestration** | None — local only | EC2 dispatcher in `infra/` (internal team) | Dockerfile included; runs in containers |

### When to pick which

- **You want the AI to use the same in-Excel tools a human analyst would** → `excel-agents-master/`. Most realistic for measuring financial-modeling capability inside Excel.
- **You want the AI to work the way a typical user would today (chat, upload PDFs, download what it makes)** → `gui-agents-master/`. Closest to the "general public" workflow.
- **You want a fully headless, scriptable, API-driven path with no browser overhead** → `cli-agents-master/`. Easiest to run at scale on a CI runner or in containers; only the OpenAI provider is wired in today.

---

## Local vs. infra — internal vs. external readers

If you're external to the BizbenchV1 team, the **local paths in each suite are designed to be self-contained** — you bring your own accounts and task lists, and everything runs on your machine. The infra paths are how our team runs the benchmark internally; the code is in the repo for transparency, but it's not turnkey for outside use without your own database, cloud setup, and (for gui) AWS account.

| Suite | Local path (everyone) | Infra path (BizbenchV1 internal team) |
|---|---|---|
| `excel-agents-master` | `batch_automation_runner.py` with local YAML tasks | `infra/run.py` — DB-driven local runner against our private Postgres + S3. **No EC2 orchestration.** |
| `gui-agents-master` | `claude_web_batch_runner.py` with local YAML tasks | `infra/dispatcher/` + `infra/worker/` — multi-box EC2 orchestration against our AWS, Postgres, and S3 |
| `cli-agents-master` | `excel-agent --batch-config examples/test_local.yaml` (just an OpenAI API key) | Same CLI with `DATABASE_URL` + AWS keys, optionally containerized via the included `Dockerfile` |
| `judge` | `bash judge/setups/setup.sh` then `judge/run.sh` against local attempts | Same scripts; some main_scripts read from / write to the internal DB |

---

## Where to go next

- **excel-agents** — [`excel-agents-master/README.md`](excel-agents-master/README.md)
- **gui-agents** — [`gui-agents-master/README.md`](gui-agents-master/README.md) (and [`gui-agents-master/infra/README.md`](gui-agents-master/infra/README.md) for the EC2 operator guide)
- **cli-agents** — [`cli-agents-master/README.md`](cli-agents-master/README.md)
- **judge** — [`judge/README.md`](judge/README.md)

---

## Repo conventions

- **Python 3.10+** for the agent suites; **Python 3.12.12** for `judge/`.
- **[uv](https://docs.astral.sh/uv/)** for environment management across the agent suites.
- **Web/desktop login auth** for excel-agents and gui-agents — no `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` needed for these. cli-agents is the API-key path.
- **Tasks are defined in YAML.** Each suite has its own task schema; the local quickstarts in each README walk through it.
- **Attempts are written either locally** (a dated output folder with `solutions/` + `json_logs/`) **or to BizbenchV1 Postgres + S3** (internal team only).
- **Architecture diagrams** live under each suite's `docs/architecture_diagram.png` with a longer-form `docs/ARCHITECTURE.md` next to them.
