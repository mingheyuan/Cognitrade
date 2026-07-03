from __future__ import annotations

from typing import Any

from research_system.services.memory_service_protocol import (
    CHAT_RESEARCH_MEMORY_TYPE,
    CHAT_RESEARCH_SNAPSHOT_VERSION,
    MEMORY_RECORD_SCHEMA_VERSION,
    METHODOLOGY_MEMORY_TYPES,
    METHODOLOGY_SNAPSHOT_VERSION,
    build_entity_scope,
    build_memory_id,
    build_memory_recorded_at,
    build_snapshot_id,
)


class MemoryServiceWriteSupport:
    def __init__(self, *, adapter) -> None:
        self.adapter = adapter

    def write_memory(
        self,
        *,
        memory_type: str,
        content: dict[str, Any],
        metadata: dict[str, Any] | None,
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
        resolved_entity_scope = str(entity_scope or "").strip() or build_entity_scope(
            memory_scope=memory_scope,
            metadata=enriched_metadata,
            content=enriched_content,
            memory_type=memory_type,
        )
        resolved_snapshot_id = str(snapshot_id or "").strip() or build_snapshot_id(
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
