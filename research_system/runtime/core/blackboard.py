from __future__ import annotations

from pathlib import Path

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl
from research_system.schemas.runtime import BlackboardEntry


class Blackboard:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.default_thread_id: str | None = None

    def bind_thread(self, thread_id: str) -> None:
        self.default_thread_id = str(thread_id or "").strip() or None

    def add(self, entry: BlackboardEntry) -> BlackboardEntry:
        if self.default_thread_id and not str(entry.thread_id or "").strip():
            entry = entry.model_copy(update={"thread_id": self.default_thread_id})
        append_jsonl(self.path, entry.model_dump(mode="json"))
        return entry

    def list_entries(
        self,
        *,
        thread_id: str | None = None,
        kind: str | None = None,
        agent: str | None = None,
        status: str | None = None,
    ) -> list[BlackboardEntry]:
        rows = [BlackboardEntry.model_validate(row) for row in read_jsonl(self.path)]
        effective_thread_id = str(thread_id or self.default_thread_id or "").strip() or None
        filtered: list[BlackboardEntry] = []
        for row in rows:
            if effective_thread_id is not None and row.thread_id != effective_thread_id:
                continue
            if kind is not None and row.kind != kind:
                continue
            if agent is not None and row.agent != agent:
                continue
            if status is not None and row.status != status:
                continue
            filtered.append(row)
        return filtered
