# Reproducing MBABench Results: Complete Setup Guide

This guide walks through setting up the environment, installing dependencies, configuring Ollama with **Gemma-4-31B-it GGUF**, and running the benchmark on **NVIDIA GPUs (CUDA 12.2)**.

---

## 1. Quick Dependency Overview

The repository dependencies are divided into three clean tiers in [`requirements.txt`](file:///shared/share_mala/aidenm/MBABench/requirements.txt):

1. **MBABench Core Python Packages**:

   - `openai` (>=1.0.0, <=2.45.0) — API communication with OpenAI or local Ollama endpoints.
   - `openpyxl` (>=3.1.0, <=3.1.5) — Excel workbook reading, writing, and cell inspection.
   - `mcp` (>=1.2.0, <=1.28.1) — FastMCP tool execution protocol.
   - `pypdf` (>=3.0.0, <=6.14.2) — PDF problem prompt extraction.
   - `PyYAML` (>=6.0.0, <=6.0.3) — YAML config parsing and trace logging.
   - `python-dotenv` (>=1.0.0, <=1.2.2) — `.env` environment loading.
   - `httpx` (>=0.27.0, <=0.28.1) — Network requests & custom timeouts.
   - `pydantic` (>=2.0.0, <=2.13.4) — Data models & validation.
2. **System Dependencies (Crucial for Excel formula recalculation)**:

   - `libreoffice` & `python3-uno` (apt packages) — Headless LibreOffice Calc engine for full formula evaluation (e.g. `IRR`, `PMT`, `XNPV`).
3. **Gemma-4-31B-it GGUF & Ollama Serving**:

   - Standalone Ollama server (with built-in CUDA 12 GPU acceleration).
   - Zero Python dependencies required for the `Modelfile` chat template (handled natively in Go by Ollama).
4. **NVIDIA GPU & CUDA 12.2**:

   - NVIDIA Driver >= 535 (CUDA 12.2 support).
   - Automatic GPU offload via Ollama's bundled CUDA 12 runtime (`libggml-cuda.so`).

---

## 2. Step-by-Step Installation

### Step A: System Prerequisites (Linux / Ubuntu)

Install LibreOffice and the system Python UNO bridge:

```bash
sudo apt-get update
sudo apt-get install -y libreoffice python3-uno
```

> [!WARNING]
> Do **NOT** install the PyPI package named `uno` (`pip install uno`). It is an unrelated dummy package. The code uses the system `/usr/bin/python3` UNO bridge installed via `python3-uno`.

---

### Step B: Create a Clean Python Environment

```bash
conda create -n mbabench python=3.10 -y
conda activate mbabench
```

---

### Step C: Install Python Dependencies & Agent Package

From the `MBABench/` directory:

```bash
# 1. Install required packages
pip install -r requirements.txt

# 2. Install the excel-cli-agent package in editable mode
pip install -e cli-agents-master
```

---

### Step D: Set Up Ollama with Gemma-4-31B-it GGUF

1. **Install Ollama**:

   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```

   *(Or download the standalone binary tarball).*
2. **Download Model Weights**:
   Download the Gemma-4-31B-it GGUF quant (e.g., `google_gemma-4-31B-it-IQ4_XS.gguf` from Hugging Face).
3. **Modelfile Configuration**:
   Create or verify your `Modelfile`:

   ```dockerfile
   FROM /path/to/google_gemma-4-31B-it-IQ4_XS.gguf
   PARAMETER num_ctx 131072
   PARAMETER stop "<end_of_turn>"

   TEMPLATE """{{ if .System }}<start_of_turn>system
   {{ .System }}<end_of_turn>
   {{ end }}{{ if .Messages }}{{ range .Messages }}<start_of_turn>{{ .Role }}
   {{ .Content }}<end_of_turn>
   {{ end }}<start_of_turn>model
   {{ else if .Prompt }}<start_of_turn>user
   {{ .Prompt }}<end_of_turn>
   <start_of_turn>model
   {{ end }}"""
   ```

   > [!NOTE]
   > **Why no chat template dependencies in Python?**
   > The `TEMPLATE` block is interpreted directly by Ollama's Go runtime (`text/template`). When `excel-cli-agent` sends messages via the standard OpenAI API, Ollama formats the prompt tokens inside its own process. No Python Jinja or tokenizers are needed.
   >
4. **Register Model with Ollama**:

   ```bash
   ollama create gemma4 -f Modelfile
   ```
5. **Start Ollama Server with GPU / Flash Attention Optimizations**:

   ```bash
   export OLLAMA_FLASH_ATTENTION=1
   export OLLAMA_KV_CACHE_TYPE=q4_0
   export OLLAMA_NUM_PARALLEL=1
   export OLLAMA_HOST=127.0.0.1:11434

   ollama serve
   ```

---

## 3. Running the Benchmark

### Test Connection

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="test-key")
res = client.chat.completions.create(
    model="gemma4",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(res.choices[0].message.content)
```

### Run Local Batch

Configure your `batch_config_local.yaml`:

```yaml
batch_name: "Gemma4-31B-it-Evaluation"
model: "bartowski/google_gemma-4-31B-it-GGUF"
local_mode: true
base_url: "http://127.0.0.1:11434/v1"

workspaces:
  - path: "/path/to/tasks/"
  - path: "/path/to/tasks/"

workspace_base_dir: "./mbabench_runs/workspaces"
results_dir: "./mbabench_runs/results"

max_iterations: 20
prompt_version: "v11"
max_completion_tokens: 90000
api_timeout_seconds: 500
fresh_context_mode: true
formatting_audit: true
```

Execute the batch run:

```bash
excel-agent --batch-config batch_config_local.yaml
```
