from __future__ import annotations

from typing import Any

from research_system.services.memory.core.protocol import (
    CHAT_RESEARCH_MEMORY_TYPE,
    CHAT_RESEARCH_SNAPSHOT_VERSION,
    MEMORY_RECORD_SCHEMA_VERSION,
    METHODOLOGY_MEMORY_TYPES,
    METHODOLOGY_SNAPSHOT_VERSION,
    build_memory_id,
    build_memory_recorded_at,
)
from research_system.services.memory.query.query_support import MemoryServiceQuerySupport
from research_system.services.memory.write.write_support import MemoryServiceWriteSupport


class MemoryService:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.write_support = MemoryServiceWriteSupport(adapter=adapter)
        self.query_support = MemoryServiceQuerySupport(adapter=adapter)

    def write_memory(
        self,
        *,
        memory_type: str,
        content: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        memory_scope: str,
        snapshot_version: str,
        entity_scope: str | None = None,
        snapshot_id: str | None = None,
        memory_id: str | None = None,
    ) -> dict[str, Any]:
        return self.write_support.write_memory(
            memory_type=memory_type,
            content=content,
            metadata=metadata,
            memory_scope=memory_scope,
            snapshot_version=snapshot_version,
            entity_scope=entity_scope,
            snapshot_id=snapshot_id,
            memory_id=memory_id,
        )

    def write_methodology_memory(
        self,
        *,
        memory_type: str,
        content: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.write_support.write_methodology_memory(
            memory_type=memory_type,
            content=content,
            metadata=metadata,
        )

    def write_chat_research_memory(
        self,
        *,
        query: str,
        intent: str,
        run_id: str,
        summary: str | None,
        conclusion: str | None,
        follow_up: str | None,
        status: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        return self.write_support.write_chat_research_memory(
            query=query,
            intent=intent,
            run_id=run_id,
            summary=summary,
            conclusion=conclusion,
            follow_up=follow_up,
            status=status,
            thread_id=thread_id,
        )

    def read_recent_memory(self, *, memory_type: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        return self.query_support.read_recent_memory(memory_type=memory_type, limit=limit)

    def read_recent_chat_memory(self, *, limit: int = 10, thread_id: str | None = None) -> list[dict[str, Any]]:
        return self.query_support.read_recent_chat_memory(limit=limit, thread_id=thread_id)

    def load_latest_chat_research_memory(self, *, thread_id: str | None = None) -> dict[str, Any] | None:
        return self.query_support.load_latest_chat_research_memory(thread_id=thread_id)

    def search_memory(
        self,
        *,
        query: str,
        memory_types: list[str] | None = None,
        limit: int = 10,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self.query_support.search_memory(
            query=query,
            memory_types=memory_types,
            limit=limit,
            metadata_filters=metadata_filters,
        )

    def search_chat_research_memory(
        self,
        *,
        query: str,
        limit: int = 10,
        thread_id: str | None = None,
        exclude_thread_id: str | None = None,
        intent: str | None = None,
        exclude_intent: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.query_support.search_chat_research_memory(
            query=query,
            limit=limit,
            thread_id=thread_id,
            exclude_thread_id=exclude_thread_id,
            intent=intent,
            exclude_intent=exclude_intent,
        )

    def read_recent_methodology_memory(
        self,
        *,
        memory_types: set[str] | None = None,
        limit_per_type: int = 5,
    ) -> list[dict[str, Any]]:
        return self.query_support.read_recent_methodology_memory(
            memory_types=memory_types,
            limit_per_type=limit_per_type,
        )

    def search_methodology_memory(
        self,
        *,
        query: str,
        memory_types: set[str] | None = None,
        limit: int = 10,
        benchmark_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.query_support.search_methodology_memory(
            query=query,
            memory_types=memory_types,
            limit=limit,
            benchmark_scope=benchmark_scope,
        )


__all__ = ["MemoryService"]
