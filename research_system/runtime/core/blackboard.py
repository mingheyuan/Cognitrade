from __future__ import annotations

from pathlib import Path

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl
from research_system.schemas.runtime import BlackboardEntry


class Blackboard:
    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, entry: BlackboardEntry) -> BlackboardEntry:
        append_jsonl(self.path, entry.model_dump(mode="json"))
        return entry

    def list_entries(
        self,
        *,
        kind: str | None = None,
        agent: str | None = None,
        status: str | None = None,
    ) -> list[BlackboardEntry]:
        rows = [BlackboardEntry.model_validate(row) for row in read_jsonl(self.path)]
        filtered: list[BlackboardEntry] = []
        for row in rows:
            if kind is not None and row.kind != kind:
                continue
            if agent is not None and row.agent != agent:
                continue
            if status is not None and row.status != status:
                continue
            filtered.append(row)
        return filtered
