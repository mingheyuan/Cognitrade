from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_settings_yaml_configs(paths_by_name: dict[str, Path]) -> dict[str, Any]:
    configs: dict[str, Any] = {}
    for name, path in paths_by_name.items():
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                configs[name] = yaml.safe_load(handle) or {}
        else:
            configs[name] = {}
    return configs
