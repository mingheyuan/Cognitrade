from __future__ import annotations

from pathlib import Path

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl
from research_system.schemas.runtime import RunEvent


class EventStream:
    def __init__(self, path: Path) -> None:
        self.path = path

    def emit(self, event: RunEvent) -> RunEvent:
        append_jsonl(self.path, event.model_dump(mode="json"))
        return event

    def read_all(self) -> list[RunEvent]:
        return [RunEvent.model_validate(row) for row in read_jsonl(self.path)]
