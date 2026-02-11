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


def load_project_configs():
    """Load project_configs.yaml and set non-null values as environment variables."""
    print(f"Loading project configs from {_CONFIG_PATH}...")
    loaded_configs = {}
    with open(_CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    for section in config.values():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if value is not None:
                print(f"Setting env var {key.upper()} = {value}")
                os.environ[key.upper()] = str(value)
                loaded_configs[key.upper()] = value

    return loaded_configs
