from __future__ import annotations

from research_system.services.memory.core.protocol import (
    METHODOLOGY_MEMORY_TYPES,
    latest_rows_by_entity_scope,
    record_sort_key,
)


class MemoryServiceMethodologyQuerySupport:
    def __init__(self, *, base_query_support) -> None:
        self.base_query_support = base_query_support

    def read_recent_methodology_memory(
        self,
        *,
        memory_types: set[str] | None = None,
        limit_per_type: int = 5,
    ) -> list[dict]:
        selected_types = memory_types or METHODOLOGY_MEMORY_TYPES
        rows: list[dict] = []
        for memory_type in selected_types:
            rows.extend(self.base_query_support.read_recent_memory(memory_type=memory_type, limit=limit_per_type))
        rows = latest_rows_by_entity_scope(rows)
        rows.sort(key=lambda row: record_sort_key(row), reverse=True)
        return rows

    def search_methodology_memory(
        self,
        *,
        query: str,
        memory_types: set[str] | None = None,
        limit: int = 10,
        benchmark_scope: str | None = None,
    ) -> list[dict]:
        selected_types = sorted(memory_types or METHODOLOGY_MEMORY_TYPES)
        metadata_filters: dict[str, object] = {"memory_scope": "methodology"}
        if benchmark_scope:
            metadata_filters["benchmark_scope"] = benchmark_scope
        rows = self.base_query_support.search_memory(
            query=query,
            memory_types=selected_types,
            limit=limit * (3 if benchmark_scope else 1),
            metadata_filters=metadata_filters,
        )
        if benchmark_scope:
            target_scope = str(benchmark_scope or "").strip()
            rows = [
                row
                for row in rows
                if str(((row.get("content") or {}) if isinstance(row.get("content"), dict) else {}).get("benchmark_scope") or "").strip() == target_scope
            ]
        rows = latest_rows_by_entity_scope(rows)
        return rows[:limit]


__all__ = ["MemoryServiceMethodologyQuerySupport"]
