from __future__ import annotations

from pathlib import Path

from research_system.runtime.storage.jsonl import append_jsonl
from research_system.schemas.runtime import LogRecord


class RunLogger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def log(self, run_id: str, level: str, component: str, message: str, context: dict | None = None) -> LogRecord:
        record = LogRecord(
            run_id=run_id,
            level=level,  # type: ignore[arg-type]
            component=component,
            message=message,
            context=context or {},
        )
        append_jsonl(self.path, record.model_dump(mode="json"))
        return record
