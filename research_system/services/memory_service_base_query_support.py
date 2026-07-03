from __future__ import annotations

from typing import Any


class MemoryServiceBaseQuerySupport:
    def __init__(self, *, adapter) -> None:
        self.adapter = adapter

    def read_recent_memory(self, *, memory_type: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        return self.adapter.read_recent(memory_type=memory_type, limit=limit)

    def search_memory(
        self,
        *,
        query: str,
        memory_types: list[str] | None = None,
        limit: int = 10,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self.adapter.search(
            query=query,
            memory_types=memory_types,
            limit=limit,
            metadata_filters=metadata_filters,
        )


__all__ = ["MemoryServiceBaseQuerySupport"]
