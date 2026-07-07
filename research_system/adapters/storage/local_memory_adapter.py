from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl

_SEARCH_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "for",
    "from",
    "how",
    "into",
    "just",
    "more",
    "previous",
    "recent",
    "review",
    "than",
    "that",
    "the",
    "this",
    "what",
    "which",
    "who",
    "with",
}


class LocalMemoryAdapter:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.export_path = settings.memory_root / "records.jsonl"
        self.summary_path = settings.memory_root / "memory_summary.md"
        self.registry_path = settings.memory_root / "MEMORY.md"

    def write(self, *, memory_type: str, content: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_content = dict(content or {})
        normalized_metadata = dict(metadata or {})
        recorded_at = str(
            normalized_content.get("recorded_at")
            or normalized_metadata.get("recorded_at")
            or ""
        ).strip() or None
        record = {
            "memory_type": memory_type,
            "content": normalized_content,
            "metadata": normalized_metadata,
            "sink": "local_memory",
            "recorded_at": recorded_at,
        }
        self.export_path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(self.export_path, record)
        self._refresh_views()
        return record

    def read_recent(self, *, memory_type: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        rows = read_jsonl(self.export_path)
        if memory_type:
            rows = [row for row in rows if str(row.get("memory_type") or "") == memory_type]
        if limit <= 0:
            return []
        rows.reverse()
        return [row for row in rows[:limit] if isinstance(row, dict)]

    def search(
        self,
        *,
        query: str,
        memory_types: list[str] | None = None,
        limit: int = 10,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query or limit <= 0:
            return []
        rows = read_jsonl(self.export_path)
        return self._score_and_filter_rows(
            rows=rows,
            query=normalized_query,
            memory_types=memory_types,
            limit=limit,
            metadata_filters=dict(metadata_filters or {}),
        )

    def _refresh_views(self) -> None:
        rows = [row for row in read_jsonl(self.export_path) if isinstance(row, dict)]
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        latest_chat = next(
            (
                row for row in reversed(rows)
                if str(row.get("metadata", {}).get("memory_scope") or "") == "chat_research"
            ),
            None,
        )
        summary_lines = ["# Memory Summary", ""]
        if latest_chat is not None:
            content = latest_chat.get("content") if isinstance(latest_chat.get("content"), dict) else {}
            summary_lines.extend(
                [
                    f"- Thread: {content.get('thread_id') or 'global'}",
                    f"- Intent: {content.get('intent') or 'unknown'}",
                    f"- Query: {content.get('query') or ''}",
                    f"- Conclusion: {content.get('conclusion') or ''}",
                    f"- Follow-up: {content.get('follow_up') or ''}",
                ]
            )
        else:
            summary_lines.append("- No chat research memory yet.")
        self.summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

        registry_lines = ["# MEMORY", ""]
        for row in reversed(rows[-50:]):
            memory_type = str(row.get("memory_type") or "").strip() or "unknown"
            content = row.get("content") if isinstance(row.get("content"), dict) else {}
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            title = (
                content.get("title")
                or content.get("query")
                or content.get("benchmark_scope")
                or metadata.get("snapshot_id")
                or "memory"
            )
            registry_lines.append(f"## {memory_type}: {title}")
            registry_lines.append("")
            registry_lines.append(f"- Recorded at: {content.get('recorded_at') or metadata.get('recorded_at') or 'unknown'}")
            registry_lines.append(f"- Scope: {metadata.get('entity_scope') or metadata.get('memory_scope') or 'unknown'}")
            text_blob = json.dumps(content, ensure_ascii=False, sort_keys=True)
            registry_lines.append(f"- Content: {text_blob[:400]}")
            registry_lines.append("")
        self.registry_path.write_text("\n".join(registry_lines) + "\n", encoding="utf-8")

    def _score_and_filter_rows(
        self,
        *,
        rows: list[dict[str, Any]],
        query: str,
        memory_types: list[str] | None,
        limit: int,
        metadata_filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        query_terms = self._extract_search_terms(query)
        if not query_terms:
            return []
        memory_type_set = {str(item).strip() for item in memory_types or [] if str(item).strip()}
        scored_rows: list[tuple[float, str, dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            memory_type_value = str(row.get("memory_type") or "").strip()
            if memory_type_set and memory_type_value not in memory_type_set:
                continue
            if not self._metadata_matches(row.get("metadata"), metadata_filters):
                continue
            content = row.get("content") if isinstance(row.get("content"), dict) else {}
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            blob = "\n".join(
                [
                    json.dumps(content, ensure_ascii=False, sort_keys=True),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                ]
            )
            matched_terms, score = self._score_query_terms(query_terms=query_terms, blob=blob)
            if score <= 0:
                continue
            enriched = dict(row)
            enriched["matched_terms"] = matched_terms
            enriched["match_score"] = round(score, 3)
            recorded_at = str(
                row.get("recorded_at")
                or (content.get("recorded_at") if isinstance(content, dict) else "")
                or ""
            )
            scored_rows.append((score, recorded_at, enriched))
        scored_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [row for _, _, row in scored_rows[:limit]]

    def _metadata_matches(self, metadata: object, filters: dict[str, Any]) -> bool:
        if not filters:
            return True
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        if not isinstance(metadata_dict, dict):
            return False
        for key, expected in filters.items():
            actual = metadata_dict.get(key)
            if isinstance(expected, (list, tuple, set)):
                normalized = {str(item).strip() for item in expected if str(item).strip()}
                if str(actual or "").strip() not in normalized:
                    return False
            else:
                if str(actual or "").strip() != str(expected or "").strip():
                    return False
        return True

    def _extract_search_terms(self, query: str) -> set[str]:
        terms: set[str] = set()
        lowered = query.lower()
        for token in re.findall(r"[a-z0-9]{2,}", lowered):
            if token not in _SEARCH_STOPWORDS:
                terms.add(token)
        for token in re.findall(r"[\u4e00-\u9fff]{2,}", query):
            terms.add(token)
            if len(token) <= 8:
                for index in range(0, len(token) - 1):
                    terms.add(token[index:index + 2])
        return {term for term in terms if term.strip()}

    def _score_query_terms(self, *, query_terms: set[str], blob: str) -> tuple[list[str], float]:
        lowered_blob = str(blob or "").lower()
        matched_terms: list[str] = []
        score = 0.0
        for term in sorted(query_terms):
            probe = term.lower()
            if probe not in lowered_blob:
                continue
            matched_terms.append(term)
            if len(term) >= 8:
                score += 3.0
            elif len(term) >= 4:
                score += 2.0
            else:
                score += 1.0
        return matched_terms, score


__all__ = ["LocalMemoryAdapter"]
