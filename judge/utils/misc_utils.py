import argparse
import os
from pathlib import Path

import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "project_configs.yaml")
_CONFIG_PATH = os.path.abspath(_CONFIG_PATH)


def get_absolute_path(path) -> str:
    """Convert a path to an absolute path."""
    return str(Path(path).resolve())


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


def load_project_configs(verbose=False):
    """Load project_configs.yaml and set non-null values as environment variables.

    Env var names follow: {PROJECT_NAME}_{SECTION}_{...}_{KEY}
    where PROJECT_NAME comes from project.name in the config.
    """
    if verbose:
        print(f"*" * 126)
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
            if verbose:
                print(f"Setting env var {env_key} = {value}")
            os.environ[env_key] = str(value)
            loaded_configs[env_key] = value
    if verbose:
        print(f"*" * 126)
    return loaded_configs, prefix


def load_env_var(var_name: str, default=None, prefix=None, required=False):
    """Helper to load an env var with optional default."""
    if prefix is None:
        with open(_CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        project_name = config.get("project", {}).get("name", "")
        prefix = project_name.upper()
    var_name = f"{prefix}_{var_name.upper()}"
    value = os.environ.get(var_name, None)

    if value is None:
        if not required:
            from .logger import logger

            logger.debug(
                f"Environment variable {var_name} not set. Using default: {default}"
            )
            value = default
        else:
            raise EnvironmentError(f"Required environment variable {var_name} not set.")
    return value


### Argparser helper
def str2bool(v):
    """Convert string to boolean for argparse."""
    if isinstance(v, bool):
        return v
    if v.lower() in ("yes", "true", "t", "y", "1"):
        return True
    elif v.lower() in ("no", "false", "f", "n", "0"):
        return False
    else:
        raise argparse.ArgumentTypeError("Boolean value expected.")


if __name__ == "__main__":
    load_project_configs()
