from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research_system.adapters.storage.mem0_adapter import Mem0Adapter
from research_system.runtime.id_utils import generate_ulid


CHAT_RESEARCH_MEMORY_TYPE = "chat_research_summary"
MEMORY_RECORD_SCHEMA_VERSION = "memory_record_v2"
CHAT_RESEARCH_SNAPSHOT_VERSION = "chat_research_v2"
METHODOLOGY_SNAPSHOT_VERSION = "methodology_v2"
METHODOLOGY_MEMORY_TYPES = {
    "methodology_summary",
    "dimension_experience",
    "error_pattern",
    "user_preference",
}


def build_memory_recorded_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_memory_id() -> str:
    return generate_ulid()


def _normalize_scope_token(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    return "".join(ch if ch.isalnum() or ch in {"-", "_", ":", "."} else "_" for ch in token)


def _build_entity_scope(*, memory_scope: str, metadata: dict[str, Any], content: dict[str, Any], memory_type: str) -> str:
    if memory_scope == "chat_research":
        thread_id = _normalize_scope_token(metadata.get("thread_id") or content.get("thread_id"))
        if thread_id:
            return f"chat_thread:{thread_id}"
        intent = _normalize_scope_token(metadata.get("intent") or content.get("intent"))
        if intent:
            return f"chat_intent:{intent}"
        run_id = _normalize_scope_token(metadata.get("run_id") or content.get("run_id"))
        if run_id:
            return f"chat_run:{run_id}"
        return "chat:global"
    benchmark_scope = _normalize_scope_token(content.get("benchmark_scope") or metadata.get("benchmark_scope"))
    if benchmark_scope:
        return f"methodology:{memory_type}:{benchmark_scope}"
    proposal_id = _normalize_scope_token(content.get("proposal_id") or metadata.get("proposal_id"))
    if proposal_id:
        return f"methodology:{memory_type}:proposal:{proposal_id}"
    return f"methodology:{memory_type}:global"


def _build_snapshot_id(*, memory_scope: str, memory_type: str, metadata: dict[str, Any], content: dict[str, Any], entity_scope: str) -> str:
    thread_id = _normalize_scope_token(metadata.get("thread_id") or content.get("thread_id"))
    run_id = _normalize_scope_token(metadata.get("run_id") or content.get("run_id"))
    proposal_id = _normalize_scope_token(content.get("proposal_id") or metadata.get("proposal_id"))
    benchmark_scope = _normalize_scope_token(content.get("benchmark_scope") or metadata.get("benchmark_scope"))
    if memory_scope == "chat_research":
        if thread_id and run_id:
            return f"chat_snapshot:{thread_id}:{run_id}"
        if run_id:
            return f"chat_snapshot:run:{run_id}"
    if proposal_id:
        return f"methodology_snapshot:proposal:{proposal_id}"
    if benchmark_scope:
        return f"methodology_snapshot:{memory_type}:{benchmark_scope}"
    return f"{entity_scope}:{build_memory_id()}"


def _record_protocol_value(row: dict[str, Any], key: str) -> str:
    content = row.get("content") if isinstance(row.get("content"), dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    content_value = str((content or {}).get(key) or "").strip()
    if content_value:
        return content_value
    return str((metadata or {}).get(key) or "").strip()


def _record_sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    recorded_at = str(row.get("recorded_at") or _record_protocol_value(row, "recorded_at") or "")
    snapshot_id = _record_protocol_value(row, "snapshot_id")
    memory_id = _record_protocol_value(row, "memory_id")
    return recorded_at, snapshot_id, memory_id


def _latest_rows_by_entity_scope(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_scope: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for row in rows:
        entity_scope = _record_protocol_value(row, "entity_scope")
        if not entity_scope:
            passthrough.append(row)
            continue
        existing = latest_by_scope.get(entity_scope)
        if existing is None or _record_sort_key(row) > _record_sort_key(existing):
            latest_by_scope[entity_scope] = row
    ordered = sorted(latest_by_scope.values(), key=_record_sort_key, reverse=True)
    passthrough.sort(key=_record_sort_key, reverse=True)
    return [*ordered, *passthrough]


class MemoryService:
    def __init__(self, adapter: Mem0Adapter) -> None:
        self.adapter = adapter

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
        recorded_at = str(content.get("recorded_at") or build_memory_recorded_at())
        protocol_memory_id = str(memory_id or "").strip() or build_memory_id()
        enriched_content = dict(content)
        enriched_metadata = dict(metadata or {})
        resolved_entity_scope = str(entity_scope or "").strip() or _build_entity_scope(
            memory_scope=memory_scope,
            metadata=enriched_metadata,
            content=enriched_content,
            memory_type=memory_type,
        )
        resolved_snapshot_id = str(snapshot_id or "").strip() or _build_snapshot_id(
            memory_scope=memory_scope,
            memory_type=memory_type,
            metadata=enriched_metadata,
            content=enriched_content,
            entity_scope=resolved_entity_scope,
        )
        protocol = {
            "memory_id": protocol_memory_id,
            "schema_version": MEMORY_RECORD_SCHEMA_VERSION,
            "snapshot_version": snapshot_version,
            "snapshot_id": resolved_snapshot_id,
            "memory_scope": memory_scope,
            "entity_scope": resolved_entity_scope,
            "recorded_at": recorded_at,
        }
        enriched_content.setdefault("recorded_at", recorded_at)
        enriched_content.setdefault("memory_id", protocol_memory_id)
        enriched_content.setdefault("schema_version", MEMORY_RECORD_SCHEMA_VERSION)
        enriched_content.setdefault("snapshot_version", snapshot_version)
        enriched_content.setdefault("snapshot_id", resolved_snapshot_id)
        enriched_metadata = {
            **protocol,
            **enriched_metadata,
        }
        return self.adapter.write(memory_type=memory_type, content=enriched_content, metadata=enriched_metadata)

    def write_methodology_memory(
        self,
        *,
        memory_type: str,
        content: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if memory_type not in METHODOLOGY_MEMORY_TYPES:
            raise ValueError(f"unsupported methodology memory_type: {memory_type}")
        enriched_metadata = dict(metadata or {})
        for key in (
            "proposal_id",
            "proposal_type",
            "title",
            "benchmark_scope",
            "sample_size",
            "reviewer",
            "review_reason",
            "created_from_run_id",
            "created_from_review_id",
            "reviewed_at",
        ):
            if enriched_metadata.get(key) in (None, "") and content.get(key) not in (None, ""):
                enriched_metadata[key] = content.get(key)
        return self.write_memory(
            memory_type=memory_type,
            content=content,
            metadata=enriched_metadata,
            memory_scope="methodology",
            snapshot_version=METHODOLOGY_SNAPSHOT_VERSION,
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
        return self.write_memory(
            memory_type=CHAT_RESEARCH_MEMORY_TYPE,
            content={
                "thread_id": thread_id,
                "query": query,
                "intent": intent,
                "run_id": run_id,
                "summary": summary,
                "conclusion": conclusion,
                "follow_up": follow_up,
            },
            metadata={
                "status": status,
                "intent": intent,
                "thread_id": thread_id,
                "run_id": run_id,
            },
            memory_scope="chat_research",
            snapshot_version=CHAT_RESEARCH_SNAPSHOT_VERSION,
        )

    def read_recent_memory(self, *, memory_type: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        return self.adapter.read_recent(memory_type=memory_type, limit=limit)

    def read_recent_chat_memory(self, *, limit: int = 10, thread_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.read_recent_memory(memory_type=CHAT_RESEARCH_MEMORY_TYPE, limit=max(limit, 1) * (4 if thread_id else 1))
        if thread_id:
            target_thread_id = str(thread_id or "").strip()
            rows = [
                row
                for row in rows
                if str(((row.get("content") or {}) if isinstance(row.get("content"), dict) else {}).get("thread_id") or (row.get("metadata") or {}).get("thread_id") or "").strip() == target_thread_id
            ]
        rows = _latest_rows_by_entity_scope(rows)
        return rows[:limit]

    def load_latest_chat_research_memory(self, *, thread_id: str | None = None) -> dict[str, Any] | None:
        rows = self.read_recent_chat_memory(limit=1, thread_id=thread_id)
        if not rows and thread_id:
            rows = self.read_recent_chat_memory(limit=1)
        if not rows:
            return None
        content = rows[0].get("content")
        if not isinstance(content, dict):
            return None
        return content

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

    def search_chat_research_memory(
        self,
        *,
        query: str,
        limit: int = 10,
        thread_id: str | None = None,
        exclude_thread_id: str | None = None,
        intent: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {"memory_scope": "chat_research"}
        if thread_id:
            filters["thread_id"] = thread_id
        if intent:
            filters["intent"] = intent
        rows = self.search_memory(
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
        rows = _latest_rows_by_entity_scope(rows)
        return rows[:limit]

    def read_recent_methodology_memory(
        self,
        *,
        memory_types: set[str] | None = None,
        limit_per_type: int = 5,
    ) -> list[dict[str, Any]]:
        selected_types = memory_types or METHODOLOGY_MEMORY_TYPES
        rows: list[dict[str, Any]] = []
        for memory_type in selected_types:
            rows.extend(self.read_recent_memory(memory_type=memory_type, limit=limit_per_type))
        rows = _latest_rows_by_entity_scope(rows)
        rows.sort(key=lambda row: _record_sort_key(row), reverse=True)
        return rows

    def search_methodology_memory(
        self,
        *,
        query: str,
        memory_types: set[str] | None = None,
        limit: int = 10,
        benchmark_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        selected_types = sorted(memory_types or METHODOLOGY_MEMORY_TYPES)
        metadata_filters: dict[str, Any] = {"memory_scope": "methodology"}
        if benchmark_scope:
            metadata_filters["benchmark_scope"] = benchmark_scope
        rows = self.search_memory(
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
        rows = _latest_rows_by_entity_scope(rows)
        return rows[:limit]
