from __future__ import annotations

from pathlib import Path

import yaml


def load_dimensions(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
