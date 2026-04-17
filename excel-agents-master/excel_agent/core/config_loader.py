"""
Configuration loader with hierarchical override support.

Supports loading from:
1. Default config file (template YAML)
2. Environment variables
3. CLI arguments

Per-task fields used by the explicit task config format:
  onedrive_path     — explicit OneDrive folder path segments
  template_file     — workbook to open (or "blank")
  upload_files      — local files to upload into the add-in
  solution_name     — base name for the output file
  local_files_base  — directory for resolving upload_files paths

Per-task fields used by the task-source shorthand format:
  file_path + task_source + task_name → constructed OneDrive path
"""

import logging
import os

import yaml

logger = logging.getLogger(__name__)

# Per-task fields that should be forwarded from the task list YAML
# into the per-task config dict (used by batch_automation_runner).
TASK_LEVEL_FIELDS = {
    "onedrive_path",
    "direct_url",
    "template_file",
    "upload_files",
    "solution_name",
    "task_source",  # per-task override of template's task_source
}


class ConfigLoader:
    """Handles configuration loading and merging."""

    @staticmethod
    def _deep_merge_dict(base: dict, override: dict) -> dict:
        """Deep merge two dicts, with override taking precedence."""
        result = {}
        all_keys = set(base.keys()) | set(override.keys())

        for key in all_keys:
            if key in override and key in base:
                if isinstance(base[key], dict) and isinstance(override[key], dict):
                    result[key] = ConfigLoader._deep_merge_dict(
                        base[key], override[key]
                    )
                else:
                    result[key] = override[key]
            elif key in override:
                result[key] = override[key]
            else:
                result[key] = base[key]

        return result

    @staticmethod
    def load_config(config_path: str) -> dict:
        """
        Load config file.

        Args:
            config_path: Path to config file

        Returns:
            dict: Config dictionary
        """
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}

        return config

    @staticmethod
    def get_retry_settings(config: dict) -> dict:
        """
        Extract retry settings from config with fallback chain:
        1. Top-level `retry:` section (preferred)
        2. Per-agent block (e.g. `claude_excel_agent:` keys, kept as a fallback)
        3. Hard-coded defaults

        Args:
            config: Config dictionary

        Returns:
            dict with keys: max_agent_attempts, max_pipeline_attempts, timeout_per_task_seconds
        """
        defaults = {
            "max_agent_attempts": 3,
            "max_pipeline_attempts": 10,
            "timeout_per_task_seconds": 7200,
        }

        # Check the top-level 'retry' section first
        retry_section = config.get("retry", {})
        if retry_section:
            return {
                "max_agent_attempts": retry_section.get(
                    "max_agent_attempts", defaults["max_agent_attempts"]
                ),
                "max_pipeline_attempts": retry_section.get(
                    "max_pipeline_attempts", defaults["max_pipeline_attempts"]
                ),
                "timeout_per_task_seconds": retry_section.get(
                    "timeout_per_task_seconds", defaults["timeout_per_task_seconds"]
                ),
            }

        # Fallback to per-agent block (older config layout)
        agent_type = config.get("agent_type", "tabai")
        agent_cfg = config.get(agent_type, {})
        return {
            "max_agent_attempts": agent_cfg.get(
                "max_agent_attempts", defaults["max_agent_attempts"]
            ),
            "max_pipeline_attempts": agent_cfg.get(
                "max_total_attempts", defaults["max_pipeline_attempts"]
            ),
            "timeout_per_task_seconds": agent_cfg.get(
                "max_sec_per_task", defaults["timeout_per_task_seconds"]
            ),
        }

    @staticmethod
    def apply_env_overrides(config: dict) -> None:
        """
        Override credential fields in config with environment variables if set.

        Supported variables:
          - ONEDRIVE_EMAIL / ONEDRIVE_PASSWORD
          - TABAI_EMAIL / TABAI_PASSWORD (for TabAI agent)
          - CLAUDE_EXCEL_AGENT_EMAIL / CLAUDE_EXCEL_AGENT_PASSWORD (for Claude agent)

        Args:
            config: Config dict to modify in-place
        """
        # Get agent type to determine which login env vars to check
        agent_type = config.get("agent_type", "tabai")
        agent_upper = agent_type.upper()

        env_mappings = [
            ("login_onedrive", "email", "ONEDRIVE_EMAIL"),
            ("login_onedrive", "password", "ONEDRIVE_PASSWORD"),
            (f"login_{agent_type}", "email", f"{agent_upper}_EMAIL"),
            (f"login_{agent_type}", "password", f"{agent_upper}_PASSWORD"),
        ]

        for section_name, key_name, env_key in env_mappings:
            env_value = os.getenv(env_key)
            if env_value:
                config.setdefault(section_name, {})
                config[section_name][key_name] = env_value

    @staticmethod
    def apply_runtime_overrides(config: dict, args) -> None:
        """
        Apply CLI argument overrides to config.

        Args:
            config: Config dict to modify in-place
            args: argparse.Namespace with CLI arguments
        """
        # Get agent type to determine which config section to use
        agent_type = config.get(
            "agent_type", "tabai"
        )  # Default to tabai for backward compat

        config.setdefault(agent_type, {})
        config[agent_type].setdefault("runtime", {})

        if hasattr(args, "no_hold") and args.no_hold:
            config[agent_type]["runtime"]["no_hold"] = True

        if hasattr(args, "max_runtime") and args.max_runtime:
            config[agent_type]["runtime"]["max_runtime"] = args.max_runtime

        if hasattr(args, "kill_all_browsers") and args.kill_all_browsers:
            config[agent_type]["runtime"]["kill_all_browsers"] = True
