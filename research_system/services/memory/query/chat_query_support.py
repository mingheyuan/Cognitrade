from __future__ import annotations

from research_system.services.memory.protocol import (
    CHAT_RESEARCH_MEMORY_TYPE,
    latest_rows_by_entity_scope,
)


class MemoryServiceChatQuerySupport:
    def __init__(self, *, base_query_support) -> None:
        self.base_query_support = base_query_support

    def read_recent_chat_memory(self, *, limit: int = 10, thread_id: str | None = None) -> list[dict]:
        rows = self.base_query_support.read_recent_memory(
            memory_type=CHAT_RESEARCH_MEMORY_TYPE,
            limit=max(limit, 1) * (4 if thread_id else 1),
        )
        if thread_id:
            target_thread_id = str(thread_id or "").strip()
            rows = [
                row
                for row in rows
                if str(((row.get("content") or {}) if isinstance(row.get("content"), dict) else {}).get("thread_id") or (row.get("metadata") or {}).get("thread_id") or "").strip() == target_thread_id
            ]
        rows = latest_rows_by_entity_scope(rows)
        return rows[:limit]

    def load_latest_chat_research_memory(self, *, thread_id: str | None = None) -> dict | None:
        rows = self.read_recent_chat_memory(limit=1, thread_id=thread_id)
        if not rows and thread_id:
            rows = self.read_recent_chat_memory(limit=1)
        if not rows:
            return None
        content = rows[0].get("content")
        if not isinstance(content, dict):
            return None
        return content

    def search_chat_research_memory(
        self,
        *,
        query: str,
        limit: int = 10,
        thread_id: str | None = None,
        exclude_thread_id: str | None = None,
        intent: str | None = None,
    ) -> list[dict]:
        filters: dict[str, object] = {"memory_scope": "chat_research"}
        if thread_id:
            filters["thread_id"] = thread_id
        if intent:
            filters["intent"] = intent
        rows = self.base_query_support.search_memory(
            query=query,
            memory_types=[CHAT_RESEARCH_MEMORY_TYPE],
            limit=limit * (3 if exclude_thread_id else 1),
            metadata_filters=filters,
        )
        if exclude_thread_id:
            excluded = str(exclude_thread_id or "").strip()
            rows = [
                row
                for row in rows
                if str(((row.get("content") or {}) if isinstance(row.get("content"), dict) else {}).get("thread_id") or (row.get("metadata") or {}).get("thread_id") or "").strip() != excluded
            ]
        rows = latest_rows_by_entity_scope(rows)
        return rows[:limit]


__all__ = ["MemoryServiceChatQuerySupport"]
