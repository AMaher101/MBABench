import os

import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "project_configs.yaml")
_CONFIG_PATH = os.path.abspath(_CONFIG_PATH)


def load_project_configs():
    """Load project_configs.yaml and set non-null values as environment variables."""
    print(f"Loading project configs from {_CONFIG_PATH}...")
    with open(_CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    for section in config.values():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if value is not None:
                print(f"Setting env var {key.upper()} = {value}")
                os.environ[key.upper()] = str(value)
