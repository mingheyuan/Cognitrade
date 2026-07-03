from __future__ import annotations

from typing import Any

from research_system.services.memory_service_base_query_support import MemoryServiceBaseQuerySupport
from research_system.services.memory_service_chat_query_support import MemoryServiceChatQuerySupport
from research_system.services.memory_service_methodology_query_support import (
    MemoryServiceMethodologyQuerySupport,
)


class MemoryServiceQuerySupport:
    def __init__(self, *, adapter) -> None:
        self.adapter = adapter
        self.base_query_support = MemoryServiceBaseQuerySupport(adapter=adapter)
        self.chat_query_support = MemoryServiceChatQuerySupport(base_query_support=self.base_query_support)
        self.methodology_query_support = MemoryServiceMethodologyQuerySupport(
            base_query_support=self.base_query_support,
        )

    def read_recent_memory(self, *, memory_type: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        return self.base_query_support.read_recent_memory(memory_type=memory_type, limit=limit)

    def search_memory(
        self,
        *,
        query: str,
        memory_types: list[str] | None = None,
        limit: int = 10,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self.base_query_support.search_memory(
            query=query,
            memory_types=memory_types,
            limit=limit,
            metadata_filters=metadata_filters,
        )

    def read_recent_chat_memory(self, *, limit: int = 10, thread_id: str | None = None) -> list[dict[str, Any]]:
        return self.chat_query_support.read_recent_chat_memory(limit=limit, thread_id=thread_id)

    def load_latest_chat_research_memory(self, *, thread_id: str | None = None) -> dict[str, Any] | None:
        return self.chat_query_support.load_latest_chat_research_memory(thread_id=thread_id)

    def search_chat_research_memory(
        self,
        *,
        query: str,
        limit: int = 10,
        thread_id: str | None = None,
        exclude_thread_id: str | None = None,
        intent: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.chat_query_support.search_chat_research_memory(
            query=query,
            limit=limit,
            thread_id=thread_id,
            exclude_thread_id=exclude_thread_id,
            intent=intent,
        )

    def read_recent_methodology_memory(
        self,
        *,
        memory_types: set[str] | None = None,
        limit_per_type: int = 5,
    ) -> list[dict[str, Any]]:
        return self.methodology_query_support.read_recent_methodology_memory(
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
        return self.methodology_query_support.search_methodology_memory(
            query=query,
            memory_types=memory_types,
            limit=limit,
            benchmark_scope=benchmark_scope,
        )
