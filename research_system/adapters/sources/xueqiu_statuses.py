from __future__ import annotations

from pathlib import Path

from research_system.runtime.storage.jsonl import read_jsonl


def load_statuses(path: Path) -> list[dict]:
    return read_jsonl(path)
