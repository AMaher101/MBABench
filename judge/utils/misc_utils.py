import os
from pathlib import Path

import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "project_configs.yaml")
_CONFIG_PATH = os.path.abspath(_CONFIG_PATH)


def relative_path_from_project_root(path) -> str:
    """Convert a path to be relative from the project root directory."""
    project_root = Path(__file__).parent.parent.resolve()
    path = Path(path)

    # interpret .. or . as relative to project root and resolve to absolute path
    absolute_path = (project_root / path).resolve()
    return absolute_path


def _flatten_dict(d, prefix=""):
    """Recursively flatten a nested dict into {PREFIX_KEY: value} pairs, skipping None values."""
    items = {}
    for key, value in d.items():
        full_key = f"{prefix}_{key.upper()}" if prefix else key.upper()
        if isinstance(value, dict):
            items.update(_flatten_dict(value, full_key))
        elif value is not None:
            items[full_key] = value
    return items


def load_project_configs():
    """Load project_configs.yaml and set non-null values as environment variables.

    Env var names follow: {PROJECT_NAME}_{SECTION}_{...}_{KEY}
    where PROJECT_NAME comes from project.name in the config.
    """
    print(f"Loading project configs from {_CONFIG_PATH}...")
    with open(_CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    project_name = config.get("project", {}).get("name", "")
    prefix = project_name.upper()

    loaded_configs = {}
    for section_name, section in config.items():
        if not isinstance(section, dict):
            continue
        section_prefix = f"{prefix}_{section_name.upper()}"
        for env_key, value in _flatten_dict(section, section_prefix).items():
            print(f"Setting env var {env_key} = {value}")
            os.environ[env_key] = str(value)
            loaded_configs[env_key] = value

    return loaded_configs, prefix


def load_env_var(var_name: str, default=None, prefix=None):
    """Helper to load an env var with optional default."""
    if prefix is None:
        with open(_CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        project_name = config.get("project", {}).get("name", "")
        prefix = project_name.upper()
    var_name = f"{prefix}_{var_name.upper()}"
    value = os.environ.get(var_name, None)

    if value is None:
        from .logger import logger

        logger.warning(
            f"Environment variable {var_name} not set. Using default: {default}"
        )
        value = default
    return value


if __name__ == "__main__":
    load_project_configs()
