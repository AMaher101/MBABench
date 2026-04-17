#!/usr/bin/env python3
"""
General-Purpose Batch Automation Runner
========================================
A flexible framework for running any automation script with multiple tasks.

Supports:
- Any Python script that accepts a config file
- Dynamic config generation per task
- JSON/YAML task definitions
- Parallel or sequential execution
- Resume capability
- Detailed logging and reporting

Usage:
    python batch_automation_runner.py --tasks tasks.json --runner-config runner_config.yaml
    python batch_automation_runner.py --tasks tasks.json --script path/to/script.py --base-config config.yaml
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Import safe logging setup for Windows compatibility
from excel_agent.core import kill_all_browser_processes
from excel_agent.core.config_loader import ConfigLoader
from excel_agent.core.file_organizer import get_run_folder_label
from excel_agent.core.logging_setup import (
    configure_safe_stdout,
    setup_basic_logging,
)

# Configure stdout/stderr for Windows Unicode compatibility (before any print statements)
configure_safe_stdout()

# Track current child process for cleanup
_current_process = None


def _force_kill_process_tree(proc, logger):
    """Kill a subprocess and ALL its children.

    Critical on Windows where proc.kill() alone leaves orphan child processes
    (Playwright browsers, etc.) that cause zombie conflicts with the next task.
    """
    pid = proc.pid
    try:
        if os.name == "nt":
            # Windows: taskkill /T kills the entire process tree, /F forces it
            result = subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                logger.warning(
                    f"   taskkill returned {result.returncode}: {result.stderr.strip()}"
                )
        else:
            # Unix: kill the entire process group
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass  # Already dead
    except Exception as e:
        logger.error(f"   Failed to kill process tree (PID {pid}): {e}")

    # Always try proc.kill() as fallback and wait for cleanup
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        logger.warning(f"   Process {pid} may still be alive after kill attempt")


def _signal_handler(signum, frame):
    """Handle Ctrl+C and kill child processes."""
    global _current_process
    if _current_process:
        _force_kill_process_tree(_current_process, logger)
        _current_process = None
    sys.exit(130)  # Standard exit code for SIGINT


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def detect_python_cmd() -> List[str]:
    """Auto-detect whether to use uv or plain python.

    Returns ["uv", "run", "python"] if uv is available, otherwise ["python"].
    This allows the same config to work on systems with or without uv.
    """
    import shutil

    if shutil.which("uv"):
        return ["uv", "run", "python"]
    return ["python"]


# Setup logging - use stderr to avoid polluting stdout (grid_run parses stdout for job IDs)
# Uses SafeStreamHandler for Windows compatibility (handles emoji encoding errors)
setup_basic_logging(
    level=logging.INFO,
    format_str="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


class TaskRunner:
    """Base class for running automation tasks."""

    def __init__(
        self,
        script_path: Path,
        template: Optional[Dict[str, Any]] = None,
        config_arg: str = "--config",
        python_cmd: List[str] = ["python"],
        env_vars: Optional[Dict[str, str]] = None,
        default_extra_args: Optional[List[str]] = None,
    ):
        """
        Initialize task runner.

        Args:
            script_path: Path to the script to run
            template: Template config applied to all tasks
            config_arg: Command-line argument for config (e.g., "--config", "-c")
            python_cmd: Python command to use (e.g., ["python"], ["uv", "run", "python"])
            env_vars: Environment variables to set
            default_extra_args: Default CLI arguments for this script (e.g., ["--no-hold"])
        """
        self.script_path = script_path
        self.template = template or {}
        self.config_arg = config_arg
        self.python_cmd = python_cmd
        self.env_vars = env_vars or {}
        self.default_extra_args = default_extra_args or []

    def _substitute_variables(self, obj: Any, variables: Dict[str, str]) -> Any:
        """Recursively substitute variables in config values."""
        if isinstance(obj, str):
            # Replace {variable_name} with actual values
            result = obj
            for key, value in variables.items():
                result = result.replace(f"{{{key}}}", str(value))
            return result
        elif isinstance(obj, dict):
            return {k: self._substitute_variables(v, variables) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._substitute_variables(item, variables) for item in obj]
        else:
            return obj

    def create_task_config(
        self, task: Dict[str, Any], agent_name: str = None
    ) -> Dict[str, Any]:
        """
        Create config for a specific task by merging template + per-task fields.

        Per-task fields that override template defaults:
            onedrive_path, direct_url, template_file, upload_files,
            solution_name, task_source

        Args:
            task: Task definition dict (from tasks YAML)
            agent_name: Agent type string (e.g., 'claude_excel_agent')

        Returns:
            Merged config dict ready for engine.py
        """
        from excel_agent.core.config_loader import TASK_LEVEL_FIELDS

        # Extract task name for completion logging and variable substitution
        task_name = task.get("_task_name", task.get("task_name", task.get("name", "")))
        variables = {"task_name": task_name, "task": task_name}  # Alias

        # Apply template with variable substitution
        config = {}
        if self.template:
            template_copy = deepcopy(self.template)

            # Add task name and agent name for completion logging
            config["task_name"] = task_name
            config["agent_name"] = agent_name

            # Set agent_type based on agent_name (for AI agent selection in engine.py)
            if agent_name:
                config["agent_type"] = agent_name

            # task_source is optional — used by the shorthand-format task
            # configs. Tasks that use the explicit `upload_files` format don't
            # need it.
            config["task_source"] = template_copy.get("task_source", "")

            # If agent_name is specified, only include that agent's config
            if agent_name and agent_name in template_copy:
                # Keep shared settings and agent-specific settings
                agent_config = template_copy[agent_name]
                config.update(
                    {
                        "file_path": template_copy.get("file_path", []),
                        "prompts": template_copy.get("prompts", []),
                        "task_source": template_copy.get("task_source", ""),
                        "prompt_version": template_copy.get("prompt_version"),
                        "local_files_base": template_copy.get("local_files_base"),
                        agent_name: agent_config,
                    }
                )
                # Ensure task_name and agent_name are preserved
                config["task_name"] = task_name
                if agent_name:
                    config["agent_name"] = agent_name
                    config["agent_type"] = agent_name
            else:
                # Include all configs (backward compatibility)
                config.update(template_copy)
                # Ensure task_name and agent_name are preserved
                config["task_name"] = task_name
                if agent_name:
                    config["agent_name"] = agent_name
                    config["agent_type"] = agent_name

            # Forward per-task fields — these override template defaults
            for field in TASK_LEVEL_FIELDS:
                if field in task:
                    config[field] = task[field]

            # Apply variable substitution
            config = self._substitute_variables(config, variables)

        return config

    def run_task(
        self,
        task: Dict[str, Any],
        task_index: int,
        dry_run: bool = False,
        keep_temp_configs: bool = False,
        default_timeout: Optional[int] = None,
        agent_name: str = None,
    ) -> bool:
        """
        Run a single task with retry logic.

        Retries are handled here (not in engine) so each attempt
        runs in a fresh subprocess with a fresh asyncio event loop,
        eliminating zombie task issues.

        Exit codes from engine:
            0 = success
            1 = agent_failure (counts toward agent attempt limit)
            2 = pipeline_failure (does NOT count toward agent limit)

        Args:
            task: Task definition
            task_index: Index of task (for logging)
            dry_run: If True, preview without executing
            keep_temp_configs: If True, keep temporary config files
            default_timeout: Default timeout in seconds (can be overridden per task)
            agent_name: Name of the agent to use

        Returns:
            True if task succeeded, False otherwise
        """
        task_name = task.get(
            "_task_name",
            task.get("task_name", task.get("name", f"Task {task_index + 1}")),
        )
        logger.info(f"\n{'='*80}")
        logger.info(f"🚀 Starting Task {task_index + 1}: {task_name}")
        logger.info(f"{'='*80}")

        # Create task-specific config
        task_config = self.create_task_config(task, agent_name)

        # Read retry limits from config (supports both new 'retry:' section and legacy format)
        retry_settings = ConfigLoader.get_retry_settings(task_config)
        max_agent_attempts = retry_settings["max_agent_attempts"]
        max_total_attempts = retry_settings["max_pipeline_attempts"]
        agent_type = task_config.get("agent_type", "tabai")
        agent_cfg = task_config.get(agent_type, {})

        # Build command (stable across retries — config file is overwritten)
        task_name_safe = task_name.replace(" ", "_")[:30]
        temp_config_path = None
        if task_config:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".yaml",
                prefix=f"task_{task_index}_{task_name_safe}_",
                dir=self.script_path.parent if self.script_path else Path.cwd(),
                delete=False,
            ) as temp_config:
                temp_config_path = Path(temp_config.name)
            logger.info(f"📝 Created temporary config: {temp_config_path.name}")

        cmd = self.python_cmd + [str(self.script_path)]
        if temp_config_path:
            cmd.extend([self.config_arg, str(temp_config_path)])
        if self.default_extra_args:
            cmd.extend(self.default_extra_args)
        if "extra_args" in task:
            extra_args = task["extra_args"]
            if isinstance(extra_args, list):
                cmd.extend(extra_args)
            elif isinstance(extra_args, dict):
                for key, value in extra_args.items():
                    cmd.append(key)
                    if value is not None and value != "":
                        cmd.append(str(value))

        if dry_run:
            logger.info("🔍 DRY RUN - Would execute:")
            logger.info(f"   Command: {' '.join(cmd)}")
            logger.info(f"   Working dir: {Path.cwd()}")
            if temp_config_path:
                logger.info(
                    f"   Config preview (first 10 keys): "
                    f"{list(task_config.keys())[:10]}"
                )
                if not keep_temp_configs:
                    temp_config_path.unlink()
            return True

        # Determine timeout for this task (check task override, then retry config, then legacy, then CLI default)
        timeout = None
        if "timeout" in task:
            timeout = task["timeout"]
        elif retry_settings.get("timeout_per_task_seconds", 0) > 0:
            timeout = retry_settings["timeout_per_task_seconds"]
        elif "max_sec_per_task" in agent_cfg:
            timeout_val = agent_cfg["max_sec_per_task"]
            timeout = timeout_val if timeout_val > 0 else None
        elif default_timeout and default_timeout > 0:
            timeout = default_timeout

        if timeout:
            logger.info(
                f"⏱️  Task timeout: {timeout} seconds " f"({timeout//60}m {timeout%60}s)"
            )
        else:
            logger.info("⏱️  Task timeout: None (unlimited)")

        # ================================================================
        # RETRY LOOP — dual counters: agent_attempts + total_attempts
        # ================================================================
        agent_attempts = 0
        total_attempts = 0
        success = False

        try:
            while (
                total_attempts < max_total_attempts
                and agent_attempts < max_agent_attempts
            ):
                total_attempts += 1

                if total_attempts > 1:
                    logger.info("=" * 60)
                    logger.info(
                        f"🔄 RETRY {total_attempts}/{max_total_attempts} "
                        f"(agent: {agent_attempts}/{max_agent_attempts})"
                    )
                    logger.info("=" * 60)
                    kill_all_browser_processes()
                    time.sleep(5)

                # Set attempt_number in config for filename suffix
                task_config["attempt_number"] = total_attempts - 1

                # Write fresh config (overwrite same file)
                if temp_config_path:
                    with open(temp_config_path, "w") as f:
                        yaml.dump(task_config, f, default_flow_style=False)

                # Run subprocess
                returncode = self._run_subprocess(cmd, timeout, task_name)

                if returncode == 0:
                    logger.info(f"✅ Task '{task_name}' completed successfully")
                    success = True
                    break
                elif returncode == 1:
                    # Agent failure — counts toward agent limit
                    agent_attempts += 1
                    logger.error(
                        f"❌ Agent failure " f"({agent_attempts}/{max_agent_attempts})"
                    )
                elif returncode == 2:
                    # Pipeline failure — does NOT count toward agent limit
                    logger.error(
                        f"❌ Pipeline failure "
                        f"(total {total_attempts}/{max_total_attempts})"
                    )
                else:
                    # Unknown failure or crash — treat as agent failure
                    agent_attempts += 1
                    logger.error(
                        f"❌ Unknown failure, returncode={returncode} "
                        f"({agent_attempts}/{max_agent_attempts})"
                    )

            # Deprecated marking: mark all JSONs except the last
            if total_attempts > 1:
                self._mark_deprecated_jsons(task_name, agent_name)

            if not success:
                logger.error(
                    f"❌ Task '{task_name}' failed after "
                    f"{total_attempts} total attempts "
                    f"({agent_attempts} agent failures)"
                )

        finally:
            if temp_config_path:
                if not keep_temp_configs and temp_config_path.exists():
                    temp_config_path.unlink()
                    logger.info("🗑️  Removed temporary config")
                elif keep_temp_configs:
                    logger.info(f"💾 Kept temporary config: {temp_config_path}")

        return success

    def _run_subprocess(
        self, cmd: List[str], timeout: Optional[int], task_name: str
    ) -> int:
        """Run a single subprocess attempt. Returns the exit code."""
        global _current_process
        proc = None
        try:
            logger.info(f"▶️  Executing: {' '.join(cmd)}")

            popen_kwargs = {}
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True

            _current_process = subprocess.Popen(
                cmd,
                cwd=Path.cwd(),
                env=({**os.environ, **self.env_vars} if self.env_vars else None),
                **popen_kwargs,
            )
            proc = _current_process

            try:
                # Poll instead of blocking wait() so Ctrl+C can interrupt.
                # On Windows, proc.wait() is a blocking C call that swallows
                # signals until the child exits.
                poll_interval = 0.5  # seconds
                elapsed = 0.0
                while proc.poll() is None:
                    time.sleep(poll_interval)
                    if timeout:
                        elapsed += poll_interval
                        if elapsed >= timeout:
                            raise subprocess.TimeoutExpired(cmd, timeout)
                returncode = proc.returncode
            finally:
                _current_process = None

            return returncode

        except subprocess.TimeoutExpired:
            logger.error(f"⏱️  Task '{task_name}' TIMED OUT after {timeout} seconds")
            logger.error("   Killing timed-out subprocess and all children...")
            if proc:
                _force_kill_process_tree(proc, logger)
            _current_process = None
            logger.error("   The task was forcefully terminated")
            kill_all_browser_processes()
            time.sleep(3)
            return 2  # Pipeline failure

        except Exception as e:
            logger.error(f"❌ Error running task '{task_name}': {e}")
            return 2  # Pipeline failure

    def _mark_deprecated_jsons(self, task_name: str, agent_name: str = None):
        """Mark all completion JSONs except the last as deprecated."""
        today = datetime.now().strftime("%Y%m%d")
        folder_label = get_run_folder_label(agent_name or "claude_excel_agent")
        json_dir = Path.cwd() / f"{today}_{folder_label}" / "json_logs"
        if not json_dir.exists():
            return

        # Find all JSONs for this task (including retries)
        safe_name = task_name.replace("/", "-").replace(" ", "_")
        matching = sorted(
            json_dir.glob(f"*{safe_name}*.json"),
            key=lambda p: p.stat().st_mtime,
        )

        if len(matching) <= 1:
            return

        for json_path in matching[:-1]:
            try:
                with open(json_path, "r") as f:
                    data = json.load(f)
                if data.get("tasks"):
                    task_entry = data["tasks"][0]
                    if task_entry.get("deprecated") is not None:
                        task_entry["deprecated"] = True
                        task_entry["deprecated_reason"] = task_entry.get(
                            "agent_failed_reason"
                        )
                with open(json_path, "w") as f:
                    json.dump(data, f, indent=2)
                logger.info(f"📝 Marked deprecated: {json_path.name}")
            except Exception as e:
                logger.warning(f"⚠️ Could not mark {json_path} as deprecated: {e}")


def load_tasks(
    task_file: Path, template_file: Path = None
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Load tasks from JSON or YAML file.

    Args:
        task_file: Path to tasks file (can contain tasks only or tasks+template)
        template_file: Optional path to separate template file

    Returns:
        Tuple of (tasks_list, template_config)
    """
    suffix = task_file.suffix.lower()

    with open(task_file, "r") as f:
        if suffix == ".json":
            data = json.load(f)
        elif suffix in [".yaml", ".yml"]:
            data = yaml.safe_load(f)
        else:
            raise ValueError(f"Unsupported file format: {suffix}")

    # Extract tasks and task_source from task file
    tasks_data = []
    task_file_source = None  # task_source defined in the tasks yaml file
    if isinstance(data, dict):
        tasks_data = data.get("tasks", [])
        # Optional shorthand identifier; example sources bundled with this
        # project are "fmwc", "modeloff", and "wallstreetprep".
        task_file_source = data.get("task_source")
    else:
        tasks_data = data if isinstance(data, list) else [data]

    # Load template from separate file if provided
    template = {}
    if template_file and template_file.exists():
        logger.info(f"📖 Loading template from separate file: {template_file}")
        with open(template_file, "r") as f:
            template_data = yaml.safe_load(f)
            template = template_data.get("template", {})
    elif isinstance(data, dict):
        # Fallback: extract template from task file (backward compatibility)
        template = data.get("template", {})

    # Override template's task_source with task file's task_source (if present)
    # This ensures the correct source is used based on which tasks file is being run
    if task_file_source:
        template["task_source"] = task_file_source
        logger.info(f"📦 Task source from tasks file: {task_file_source}")

    # Normalize tasks - support both string and dict formats
    normalized_tasks = []
    for task in tasks_data:
        if isinstance(task, str):
            # Simple string format - just the task folder name
            normalized_tasks.append({"_task_name": task})
        elif isinstance(task, dict):
            # Full dict format
            normalized_tasks.append(task)
        else:
            logger.warning(f"Skipping invalid task format: {task}")

    return normalized_tasks, template


def load_runner_config(config_file: Path) -> Dict[str, Any]:
    """Load runner configuration from YAML file."""
    with open(config_file, "r") as f:
        return yaml.safe_load(f) or {}


def write_batch_summary(
    results: list,
    start_time: datetime,
    end_time: datetime,
    agent_name: str = None,
) -> Path | None:
    """
    Write a human-readable batch summary text file.

    Output goes to the date-organized run directory ({YYYYMMDD}_{agentLabel}/)
    that FileOrganizer creates during the run. Completion JSONs in
    json_logs/ are parsed for detailed failure reasons.

    Directory structure:
        {YYYYMMDD}_{agentLabel}/
        ├── solutions/
        ├── general_logs/
        ├── json_logs/          ← completion JSONs written here directly
        └── batch_summary.txt   ← written by this function

    Args:
        results: List of per-task result dicts with task_name, success, duration_seconds.
        start_time: Batch run start time.
        end_time: Batch run end time.
        agent_name: Agent type string for folder label lookup.

    Returns:
        Path to the summary file, or None if it couldn't be written.
    """
    # Find the date-organized run directory (same as FileOrganizer / engine)
    date_str = start_time.strftime("%Y%m%d")
    folder_label = get_run_folder_label(agent_name or "claude_excel_agent")
    run_dir = Path.cwd() / f"{date_str}_{folder_label}"

    if not run_dir.exists():
        logger.warning(
            f"Run directory {run_dir} not found for batch summary "
            f"(FileOrganizer may not have created it yet)"
        )
        return None

    json_logs_dir = run_dir / "json_logs"

    # Parse completion JSONs to get detailed per-task status
    task_details = {}  # task_name -> {status, reason, duration, attempt}
    if json_logs_dir.exists():
        for json_file in sorted(json_logs_dir.glob("completion_*.json")):
            try:
                with open(json_file) as f:
                    data = json.load(f)
                for task_entry in data.get("tasks", []):
                    name = task_entry.get("task_name", "unknown")
                    deprecated = task_entry.get("deprecated", False)
                    if deprecated:
                        continue  # Skip deprecated attempts
                    task_details[name] = {
                        "status": task_entry.get("task_status", "unknown"),
                        "reason": task_entry.get("agent_failed_reason"),
                        "duration": task_entry.get("duration_seconds"),
                        "attempt": task_entry.get("attempt_number", 1),
                        "error": task_entry.get("error"),
                    }
            except Exception as e:
                logger.debug(f"Failed to parse {json_file.name}: {e}")

    # Build summary text
    succeeded = sum(1 for r in results if r["success"])
    failed = sum(1 for r in results if not r["success"])
    total = len(results)
    batch_duration = (end_time - start_time).total_seconds()

    lines = []
    timestamp = end_time.strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"Batch Run Summary — {timestamp}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Total tasks:  {total}")
    lines.append(f"Succeeded:    {succeeded}")
    lines.append(f"Failed:       {failed}")
    lines.append(f"Total time:   {batch_duration / 60:.1f} min")
    lines.append("")
    lines.append("-" * 70)
    lines.append(f"{'#':<4} {'Task Name':<40} {'Status':<18} {'Time':>8}")
    lines.append("-" * 70)

    for i, task_result in enumerate(results, 1):
        name = task_result.get("task_name", "unknown")
        success = task_result.get("success", False)
        duration = task_result.get("duration_seconds", 0)
        duration_str = f"{duration / 60:.1f}m"

        # Get detailed status from completion JSON
        detail = task_details.get(name, {})
        if success:
            status_str = "SUCCESS"
        elif detail.get("status"):
            status_str = detail["status"].upper()
        else:
            status_str = "FAILED"

        lines.append(f"{i:<4} {name:<40} {status_str:<18} {duration_str:>8}")

    # Failed task details section
    failed_tasks = [t for t in results if not t.get("success", False)]
    if failed_tasks:
        lines.append("")
        lines.append("=" * 70)
        lines.append("FAILED TASK DETAILS")
        lines.append("=" * 70)

        for task_result in failed_tasks:
            name = task_result.get("task_name", "unknown")
            detail = task_details.get(name, {})
            status = detail.get("status", "unknown")
            reason = detail.get("reason") or detail.get("error") or status
            attempt = detail.get("attempt")

            lines.append("")
            lines.append(f"  Task:    {name}")
            lines.append(f"  Status:  {status}")
            lines.append(f"  Reason:  {reason or 'Process exited with non-zero code'}")
            if attempt:
                lines.append(f"  Attempt: {attempt}")

    lines.append("")

    # Write to run directory
    summary_path = run_dir / "batch_summary.txt"
    try:
        with open(summary_path, "w") as f:
            f.write("\n".join(lines))
        logger.info(f"Batch summary written to: {summary_path}")
        return summary_path
    except Exception as e:
        logger.error(f"Failed to write batch summary: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="General-Purpose Batch Automation Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--tasks", type=Path, required=True, help="Path to tasks file (JSON or YAML)"
    )

    # Option 1: Use a runner config file (auto-detected if not provided)
    parser.add_argument(
        "--runner-config",
        type=Path,
        help="Path to runner configuration file (YAML) - auto-detected if not provided",
    )

    # Option 2: Specify individual parameters (simpler for quick use)
    parser.add_argument("--script", type=Path, help="Path to script to run")
    parser.add_argument(
        "--config-arg",
        default="--config",
        help="Config argument flag (default: --config)",
    )
    parser.add_argument(
        "--python-cmd",
        default=None,
        help="Python command (e.g., 'python', 'uv run python')",
    )

    # Execution options
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview tasks without executing them"
    )
    parser.add_argument(
        "--keep-temp-configs",
        action="store_true",
        help="Keep temporary config files after execution",
    )
    parser.add_argument(
        "--stop-on-error", action="store_true", help="Stop execution if any task fails"
    )
    parser.add_argument(
        "--start-from", type=int, default=0, help="Start from task number (0-indexed)"
    )
    parser.add_argument(
        "--max-sec-per-task",
        type=int,
        default=0,
        help="Maximum seconds per task (0 = no limit, can be overridden in config)",
    )

    args = parser.parse_args()

    # Validate paths
    if not args.tasks.exists():
        logger.error(f"❌ Tasks file not found: {args.tasks}")
        sys.exit(1)

    # Auto-detect runner config if not provided
    if not args.runner_config:
        # Try to auto-detect based on tasks file location
        tasks_dir = args.tasks.parent
        if tasks_dir.name == "tasks_configs":
            # Look for runner configs in the runner_configs directory
            runner_configs_dir = tasks_dir.parent / "runner_configs"
            if runner_configs_dir.exists():
                # Check for common runner configs
                possible_configs = [
                    "tabai.yaml",
                    "claude.yaml",
                ]
                for config_name in possible_configs:
                    config_path = runner_configs_dir / config_name
                    if config_path.exists():
                        args.runner_config = config_path
                        logger.info(f"🔍 Auto-detected runner config: {config_path}")
                        break

        if not args.runner_config:
            logger.warning("⚠️ No runner config provided and auto-detection failed")
            logger.info("💡 Use --runner-config to specify a runner config file")

    # Load runner configuration
    runner_config = {}
    if args.runner_config:
        if not args.runner_config.exists():
            logger.error(f"❌ Runner config file not found: {args.runner_config}")
            sys.exit(1)
        logger.info(f"📖 Loading runner config from: {args.runner_config}")
        runner_config = load_runner_config(args.runner_config)

    # Override with command-line arguments
    script_path = args.script or Path(runner_config.get("script", ""))
    config_arg = args.config_arg or runner_config.get("config_arg", "--config")
    # Determine Python command: CLI > config > auto-detect
    if args.python_cmd:
        python_cmd = args.python_cmd.split()
    elif "python_cmd" in runner_config:
        config_cmd = runner_config["python_cmd"]
        # Support "auto" as a special value for auto-detection
        if config_cmd == "auto" or config_cmd == ["auto"]:
            python_cmd = detect_python_cmd()
        else:
            python_cmd = config_cmd if isinstance(config_cmd, list) else [config_cmd]
    else:
        # No config specified - auto-detect
        python_cmd = detect_python_cmd()

    logger.info(f"🐍 Python command: {' '.join(python_cmd)}")

    if not script_path or not script_path.exists():
        logger.error(f"❌ Script file not found: {script_path}")
        logger.error("Specify --script or use --runner-config with script path")
        sys.exit(1)

    # Load tasks and template
    logger.info(f"📋 Loading tasks from: {args.tasks}")

    # Load template file specified in runner config
    template_file = None
    template_filename = runner_config.get("template")

    if template_filename:
        # Resolve the template path. We try several candidates so that the
        # same runner-config value works regardless of where the user puts
        # their tasks file (directly under tasks_configs/, in examples/,
        # somewhere outside the repo, etc.).
        template_path = Path(template_filename)
        tasks_dir = args.tasks.parent
        repo_root = Path(__file__).resolve().parent

        if template_path.is_absolute():
            candidates = [template_path]
        else:
            candidates = [
                tasks_dir / template_path,  # alongside tasks file
                tasks_dir.parent / template_path,  # one level up (handles examples/)
                repo_root / "tasks_configs" / template_path,  # canonical location
                repo_root / template_path,  # repo-root relative
                Path.cwd() / template_path,  # CWD-relative
            ]

        template_file = next((c for c in candidates if c.exists()), None)

        if template_file is None:
            logger.error(f"❌ Template file not found: {template_filename}")
            logger.error(f"   Specified in runner config: {template_filename}")
            logger.error("   Searched the following locations:")
            for c in candidates:
                logger.error(f"     - {c}")
            sys.exit(1)

        logger.info(f"📋 Using template: {template_file}")
    else:
        # Fallback: Look for deprecated template files (backward compatibility)
        logger.warning(
            "⚠️ No 'template' specified in runner config, searching for deprecated templates..."
        )
        logger.warning(
            "   RECOMMENDED: Add 'template: \"template_tabai.yaml\"' to your runner config"
        )
        tasks_dir = args.tasks.parent
        template_candidates = [
            tasks_dir / "template_deprecated.yaml",  # New deprecated name
            tasks_dir / "template.yaml",  # Old name (if user hasn't migrated)
            tasks_dir / "template.yml",
        ]
        for candidate in template_candidates:
            if candidate.exists():
                template_file = candidate
                logger.warning(f"⚠️  Using deprecated template: {candidate.name}")
                logger.warning(
                    "   Migrate to template_tabai.yaml or template_claude.yaml for multi-source support"
                )
                break

    tasks, template = load_tasks(args.tasks, template_file)
    logger.info(f"✅ Loaded {len(tasks)} task(s)")

    if not template:
        logger.error("❌ No template configuration found")
        logger.error("   Add 'template: \"template_tabai.yaml\"' to your runner config")
        sys.exit(1)

    # Extract agent_type from template (single source of truth)
    agent_type = template.get("agent_type", "tabai")

    # Use agent_type directly as agent_name (no mapping needed)
    agent_name = agent_type

    logger.info(f"📋 Template keys: {list(template.keys())}")
    logger.info(f"🎯 Target script: {script_path}")
    logger.info(f"🤖 Agent: {agent_name} (type: {agent_type})")

    runner = TaskRunner(
        script_path=script_path,
        template=template,
        config_arg=config_arg,
        python_cmd=python_cmd,
        env_vars=runner_config.get("env_vars", {}),
        default_extra_args=runner_config.get("default_extra_args", []),
    )

    if args.start_from > 0:
        logger.info(f"⏭️  Skipping first {args.start_from} task(s)")
        tasks = tasks[args.start_from :]

    # Run tasks
    start_time = datetime.now()
    results = []
    consecutive_failures = 0

    for i, task in enumerate(tasks, start=args.start_from):
        logger.info(f"\n📊 Progress: Task {i+1}/{args.start_from + len(tasks)}")

        # Restart Chrome if too many consecutive failures (likely unresponsive)
        if consecutive_failures >= 3:
            logger.warning(
                f"⚠️ {consecutive_failures} consecutive failures — restarting Chrome before next task..."
            )
            kill_all_browser_processes()
            time.sleep(5)
            consecutive_failures = 0

        task_start_time = datetime.now()
        success = runner.run_task(
            task=task,
            task_index=i,
            dry_run=args.dry_run,
            keep_temp_configs=args.keep_temp_configs,
            default_timeout=(
                args.max_sec_per_task if args.max_sec_per_task > 0 else None
            ),
            agent_name=agent_name,
        )

        task_end_time = datetime.now()
        task_duration = (task_end_time - task_start_time).total_seconds()

        results.append(
            {
                "task_name": task.get(
                    "_task_name", task.get("task_name", task.get("name", f"Task {i+1}"))
                ),
                "success": success,
                "duration_seconds": task_duration,
            }
        )

        if success:
            consecutive_failures = 0
        else:
            consecutive_failures += 1

        if not success and args.stop_on_error:
            logger.error("❌ Stopping due to task failure (--stop-on-error enabled)")
            break

    # Print summary
    end_time = datetime.now()
    duration = end_time - start_time

    logger.info(f"\n{'='*80}")
    logger.info("📊 BATCH EXECUTION SUMMARY")
    logger.info(f"{'='*80}")
    logger.info(f"⏱️  Total duration: {duration}")
    logger.info(
        f"✅ Successful: {sum(1 for r in results if r['success'])}/{len(results)}"
    )
    logger.info(
        f"❌ Failed: {sum(1 for r in results if not r['success'])}/{len(results)}"
    )

    if not args.dry_run:
        logger.info("\nTask Results:")
        for i, result in enumerate(results, start=args.start_from):
            status = "✅" if result["success"] else "❌"
            logger.info(f"  {status} Task {i+1}: {result['task_name']}")

        # Browser sessions are preserved for future use
        # No automatic cleanup - sessions should persist across runs
        logger.info("\n💾 Browser sessions preserved for future use")

        # Write batch summary
        write_batch_summary(results, start_time, end_time, agent_name)

    # Exit with error code if any tasks failed
    if any(not r["success"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
