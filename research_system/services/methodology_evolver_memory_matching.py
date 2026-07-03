from __future__ import annotations

from typing import Any


class MethodologyEvolverMemoryMatching:
    def __init__(self, *, protocol_support) -> None:
        self.protocol_support = protocol_support

    def matching_memory_context(
        self,
        *,
        memory_rows: list[dict[str, Any]],
        memory_search_rows: list[dict[str, Any]],
        title: str,
        dimension_family: str | None = None,
        methodology_type: str | None = None,
    ) -> list[dict[str, Any]]:
        lowered_title = title.strip().lower()
        matched: list[dict[str, Any]] = []
        for row in memory_search_rows:
            content = row.get("content") if isinstance(row.get("content"), dict) else {}
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if not isinstance(content, dict):
                continue
            content_scope = str(content.get("benchmark_scope") or metadata.get("benchmark_scope") or "").strip().lower()
            if dimension_family and content_scope and content_scope != str(dimension_family).strip().lower():
                continue
            if methodology_type and content_scope and methodology_type not in content_scope:
                continue
            matched.append(
                {
                    "memory_type": row.get("memory_type"),
                    "title": content.get("title"),
                    "summary": content.get("summary"),
                    "benchmark_scope": content.get("benchmark_scope") or metadata.get("benchmark_scope"),
                    "match_score": row.get("match_score"),
                    "matched_terms": row.get("matched_terms") or [],
                    "memory_scope": self.protocol_support.protocol_value(row, "memory_scope"),
                    "entity_scope": self.protocol_support.protocol_value(row, "entity_scope"),
                    "snapshot_id": self.protocol_support.protocol_value(row, "snapshot_id"),
                    "schema_version": self.protocol_support.protocol_value(row, "schema_version"),
                    "snapshot_version": self.protocol_support.protocol_value(row, "snapshot_version"),
                    "recorded_at": self.protocol_support.protocol_value(row, "recorded_at"),
                }
            )
        for row in memory_rows:
            content = row.get("content") if isinstance(row.get("content"), dict) else {}
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if not isinstance(content, dict):
                continue
            blob = " ".join(
                str(content.get(key) or "")
                for key in ("title", "summary", "benchmark_scope", "proposal_id")
            ).lower()
            if lowered_title and lowered_title in blob:
                matched.append(
                    {
                        "memory_type": row.get("memory_type"),
                        "title": content.get("title"),
                        "summary": content.get("summary"),
                        "benchmark_scope": content.get("benchmark_scope") or metadata.get("benchmark_scope"),
                        "memory_scope": self.protocol_support.protocol_value(row, "memory_scope"),
                        "entity_scope": self.protocol_support.protocol_value(row, "entity_scope"),
                        "snapshot_id": self.protocol_support.protocol_value(row, "snapshot_id"),
                        "schema_version": self.protocol_support.protocol_value(row, "schema_version"),
                        "snapshot_version": self.protocol_support.protocol_value(row, "snapshot_version"),
                        "recorded_at": self.protocol_support.protocol_value(row, "recorded_at"),
                    }
                )
        return matched[:3]


__all__ = ["MethodologyEvolverMemoryMatching"]
