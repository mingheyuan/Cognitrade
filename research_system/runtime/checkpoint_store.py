from __future__ import annotations

from pathlib import Path

from research_system.runtime.jsonl import read_json, write_json
from research_system.schemas.runtime import RunStateSnapshot


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, snapshot: RunStateSnapshot) -> None:
        write_json(self.path, snapshot.model_dump(mode="json"))

    def load(self) -> RunStateSnapshot | None:
        payload = read_json(self.path)
        if payload is None:
            return None
        return RunStateSnapshot.model_validate(payload)

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()
