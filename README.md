# MBABench

Evaluating LLM agents on end-to-end spreadsheet tasks in finance.

[Paper (arXiv:2605.22664)](https://arxiv.org/abs/2605.22664) · [mbabench.org](https://mbabench.org/)

MBABench evaluates whether LLM agents can build a complete, working financial model in Excel from a case prompt and supporting documents, the way an analyst would, rather than answering questions about an existing sheet or editing a single formula.

## Why

Frontier labs now ship agents that construct entire spreadsheets from high-level instructions. Finance is where that matters most: modeling, forecasting, and scenario analysis run almost entirely in spreadsheets. Existing spreadsheet benchmarks test question-answering or single-formula edits, so they cannot tell you whether an agent can deliver a model a professional would accept. MBABench is one of the first benchmarks to evaluate agents on full, end-to-end financial workflows.

## How attempts are scored

A correct final number is not enough. Spreadsheets are reviewed and revised by stakeholders, so MBABench grades each attempt on three dimensions, each with fine-grained criteria from professional practice:

| Dimension | What it measures                                                                                                              |
| --------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Accuracy  | Workbook correctness, from the soundness of the underlying computation to the completeness of the required scenario analyses. |
| Formula   | The robustness and interpretability of cell-level computations.                                                               |
| Format    | The aspects that affect a spreadsheet's readability and structural clarity.                                                   |

Each dimension breaks down into fine-grained subdimensions with concrete success criteria. The `judge/` suite applies this rubric with an LLM grader and returns a weighted per-dimension and final score from 0 to 100. Its judgments were validated against finance experts and align closely with theirs.

## Three ways an agent can work on Excel

The same task runs through three interaction surfaces, each emitting one comparable Excel file so a single judge can grade them side by side:

| Surface                | How the agent works                                                                  | Mirrors                          |
| ---------------------- | ------------------------------------------------------------------------------------ | -------------------------------- |
| `excel-agents-master/` | Drives a live Excel Online session through add-in panels (TabAI, Claude, ChatGPT)    | An analyst using AI inside Excel |
| `gui-agents-master/`   | Uploads files into Claude.ai or ChatGPT and downloads the workbook the model returns | How most people use AI today     |
| `cli-agents-master/`   | OpenAI API plus an Excel MCP server, headless, LibreOffice for recalculation         | Scriptable evaluation at scale   |

## What we found

The Claude family leads the benchmark and produces the most professional-looking outputs in our qualitative review. Even so, the strongest agents frequently fall short of professional finance standards and degrade sharply once a task chains more than a few calculations. Reliable, professional-quality spreadsheet modeling remains out of reach for current agents.

## Data

- Modeloff data are available here: https://huggingface.co/datasets/namkoong-lab/mbabench-modeloff/tree/main
- FMWC and WSP data are available on https://fmworldcup.com/ and https://www.wallstreetprep.com/


## Repository

| Path                   | Contents                                                 |
| ---------------------- | -------------------------------------------------------- |
| `AGENTS.md`            | Orientation across the suites and a guide to picking one |
| `excel-agents-master/` | In-Excel add-in agents (Excel Online via OneDrive)       |
| `gui-agents-master/`   | Web chat UI agents (Claude.ai, ChatGPT)                  |
| `cli-agents-master/`   | Headless API agent (OpenAI plus Excel MCP server)        |
| `judge/`               | LLM grader for attempts from any suite                   |

Start with `AGENTS.md` for the feature matrix and the "which suite should I pick?" guide, then follow the quickstart in that suite. Each suite produces one Excel file per task; grade them with `judge/`:

```bash
bash judge/setups/setup.sh
source judge/project_configs.sh
python judge/main_scripts/judge.py -f judge/scratch/test_cases/Bread_And_Butter
```

## Citation

```bibtex
@misc{mbabench2026,
  title         = {{MBAB}ench: {E}valuating {LLM} {A}gents on {E}nd-to-{E}nd
                   {S}preadsheet {T}asks in {F}inance},
  author        = {Yen, Thomson and Poeltl, Julian and Gear, Harshith Srinivas
                   and Meng, Yilin and Fan, Joshua and Shen, Adam and Liu, Yili
                   and Bauyrzhan, Ali and Du, Siri and Liu, Haoyang
                   and Guetta, Daniel and Namkoong, Hongseok},
  year          = {2026},
  eprint        = {2605.22664},
  archivePrefix = {arXiv},
  primaryClass  = {cs.AI},
  url           = {https://arxiv.org/abs/2605.22664}
}
```

## Authors

Thomson Yen, Julian Poeltl, Harshith Srinivas Gear, Yilin Meng, Joshua Fan, Adam Shen, Yili Liu, Ali Bauyrzhan, Siri Du, Haoyang Liu, Daniel Guetta, and Hongseok Namkoong (Namkoong Lab).

## License

Released under the MIT License. See [LICENSE](LICENSE).
