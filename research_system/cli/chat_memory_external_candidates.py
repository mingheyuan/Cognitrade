from __future__ import annotations

from typing import Any

from research_system.cli.chat_memory_match_support import trim_context_text
from research_system.services.memory_service import MemoryService

_RELEVANT_CHAT_MEMORY_SCAN_LIMIT = 12
_RELEVANT_METHODOLOGY_SCAN_LIMIT = 8


def build_cross_thread_memory_candidates(
    *,
    memory_service: MemoryService,
    normalized_query: str,
    thread_id: str,
) -> list[tuple[float, dict[str, Any]]]:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for row in memory_service.search_chat_research_memory(
        query=normalized_query,
        limit=_RELEVANT_CHAT_MEMORY_SCAN_LIMIT,
        exclude_thread_id=thread_id,
    ):
        content = row.get("content") if isinstance(row.get("content"), dict) else {}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if not isinstance(content, dict):
            continue
        candidates.append(
            (
                float(row.get("match_score") or 0.0),
                {
                    "type": "relevant_chat_memory",
                    "thread_id": str(content.get("thread_id") or "").strip() or None,
                    "query": str(content.get("query") or "").strip() or None,
                    "intent": str(content.get("intent") or "").strip() or None,
                    "summary": trim_context_text(str(content.get("summary") or "").strip()) or None,
                    "conclusion": trim_context_text(str(content.get("conclusion") or "").strip()) or None,
                    "run_id": str(content.get("run_id") or "").strip() or None,
                    "memory_scope": str(metadata.get("memory_scope") or content.get("memory_scope") or "").strip() or None,
                    "snapshot_id": str(metadata.get("snapshot_id") or content.get("snapshot_id") or "").strip() or None,
                    "schema_version": str(metadata.get("schema_version") or content.get("schema_version") or "").strip() or None,
                    "matched_terms": list(row.get("matched_terms") or []),
                    "match_score": float(row.get("match_score") or 0.0),
                },
            )
        )
    return candidates


def build_methodology_memory_candidates(
    *,
    memory_service: MemoryService,
    normalized_query: str,
) -> list[tuple[float, dict[str, Any]]]:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for row in memory_service.search_methodology_memory(
        query=normalized_query,
        limit=_RELEVANT_METHODOLOGY_SCAN_LIMIT,
    ):
        content = row.get("content") if isinstance(row.get("content"), dict) else {}
        if not isinstance(content, dict):
            continue
        candidates.append(
            (
                float(row.get("match_score") or 0.0),
                {
                    "type": "relevant_methodology_memory",
                    "memory_type": str(row.get("memory_type") or "").strip() or None,
                    "title": str(content.get("title") or "").strip() or None,
                    "summary": trim_context_text(str(content.get("summary") or "").strip()) or None,
                    "benchmark_scope": str(content.get("benchmark_scope") or "").strip() or None,
                    "proposal_id": str(content.get("proposal_id") or "").strip() or None,
                    "matched_terms": list(row.get("matched_terms") or []),
                    "match_score": float(row.get("match_score") or 0.0),
                },
            )
        )
    return candidates


__all__ = [
    "build_cross_thread_memory_candidates",
    "build_methodology_memory_candidates",
]
