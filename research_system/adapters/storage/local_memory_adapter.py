from __future__ import annotations

import json
import re
from datetime import datetime, timezone
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

_QUERY_FIELD_WEIGHT = 2.6
_TITLE_FIELD_WEIGHT = 2.2
_SUMMARY_FIELD_WEIGHT = 3.1
_CONCLUSION_FIELD_WEIGHT = 3.4
_FOLLOW_UP_FIELD_WEIGHT = 1.6
_SCOPE_FIELD_WEIGHT = 1.5
_INTENT_FIELD_WEIGHT = 1.1
_FALLBACK_BLOB_WEIGHT = 0.2
_INTENT_MATCH_BONUS = 2.25


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
        deduped_rows: dict[str, tuple[float, str, dict[str, Any]]] = {}
        passthrough_rows: list[tuple[float, str, dict[str, Any]]] = []
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
            recorded_at = self._resolve_recorded_at(row=row, content=content, metadata=metadata)
            matched_terms, base_score = self._score_query_fields(
                query_terms=query_terms,
                content=content,
                metadata=metadata,
            )
            if base_score <= 0:
                continue
            score = base_score * self._time_decay_multiplier(recorded_at)
            score += self._intent_match_bonus(metadata_filters=metadata_filters, content=content, metadata=metadata)
            enriched = dict(row)
            enriched["matched_terms"] = matched_terms
            enriched["match_score"] = round(score, 3)
            dedupe_key = self._dedupe_key(content=content, metadata=metadata)
            candidate = (score, recorded_at, enriched)
            if not dedupe_key:
                passthrough_rows.append(candidate)
                continue
            existing = deduped_rows.get(dedupe_key)
            if existing is None or self._candidate_sort_key(candidate) > self._candidate_sort_key(existing):
                deduped_rows[dedupe_key] = candidate
        scored_rows = [*deduped_rows.values(), *passthrough_rows]
        scored_rows.sort(key=self._candidate_sort_key, reverse=True)
        return [row for _, _, row in scored_rows[:limit]]

    def _metadata_matches(self, metadata: object, filters: dict[str, Any]) -> bool:
        if not filters:
            return True
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        if not isinstance(metadata_dict, dict):
            return False
        for key, expected in filters.items():
            if key in {"preferred_intent"}:
                continue
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

    def _score_query_fields(
        self,
        *,
        query_terms: set[str],
        content: dict[str, Any],
        metadata: dict[str, Any],
    ) -> tuple[list[str], float]:
        field_specs = [
            (str(content.get("query") or ""), _QUERY_FIELD_WEIGHT),
            (str(content.get("title") or ""), _TITLE_FIELD_WEIGHT),
            (str(content.get("summary") or ""), _SUMMARY_FIELD_WEIGHT),
            (str(content.get("conclusion") or ""), _CONCLUSION_FIELD_WEIGHT),
            (str(content.get("follow_up") or ""), _FOLLOW_UP_FIELD_WEIGHT),
            (str(content.get("benchmark_scope") or metadata.get("benchmark_scope") or ""), _SCOPE_FIELD_WEIGHT),
            (str(content.get("intent") or metadata.get("intent") or ""), _INTENT_FIELD_WEIGHT),
        ]
        matched_terms: set[str] = set()
        score = 0.0
        for blob, weight in field_specs:
            field_matches, field_score = self._score_query_terms(query_terms=query_terms, blob=blob)
            if field_score <= 0:
                continue
            matched_terms.update(field_matches)
            score += field_score * weight
        fallback_blob = "\n".join(
            [
                json.dumps(content, ensure_ascii=False, sort_keys=True),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ]
        )
        fallback_matches, fallback_score = self._score_query_terms(query_terms=query_terms, blob=fallback_blob)
        if fallback_score > 0:
            matched_terms.update(fallback_matches)
            score += fallback_score * _FALLBACK_BLOB_WEIGHT
        return sorted(matched_terms), score

    def _intent_match_bonus(
        self,
        *,
        metadata_filters: dict[str, Any],
        content: dict[str, Any],
        metadata: dict[str, Any],
    ) -> float:
        expected_intent = str(
            metadata_filters.get("preferred_intent")
            or metadata_filters.get("intent")
            or ""
        ).strip()
        if not expected_intent:
            return 0.0
        actual_intent = str(content.get("intent") or metadata.get("intent") or "").strip()
        if actual_intent != expected_intent:
            return 0.0
        return _INTENT_MATCH_BONUS

    def _resolve_recorded_at(
        self,
        *,
        row: dict[str, Any],
        content: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        return str(
            row.get("recorded_at")
            or content.get("recorded_at")
            or metadata.get("recorded_at")
            or ""
        ).strip()

    def _time_decay_multiplier(self, recorded_at: str) -> float:
        parsed = self._parse_recorded_at(recorded_at)
        if parsed is None:
            return 1.0
        age_days = max((datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0, 0.0)
        if age_days <= 1:
            return 1.35
        if age_days <= 7:
            return 1.25
        if age_days <= 30:
            return 1.15
        if age_days <= 90:
            return 1.05
        return max(0.8, 1.0 - min(age_days / 730.0, 0.2))

    def _parse_recorded_at(self, value: str) -> datetime | None:
        token = str(value or "").strip()
        if not token:
            return None
        normalized = token[:-1] + "+00:00" if token.endswith("Z") else token
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _dedupe_key(self, *, content: dict[str, Any], metadata: dict[str, Any]) -> str | None:
        entity_scope = str(metadata.get("entity_scope") or content.get("entity_scope") or "").strip()
        snapshot_id = str(metadata.get("snapshot_id") or content.get("snapshot_id") or "").strip()
        thread_id = str(content.get("thread_id") or metadata.get("thread_id") or "").strip()
        run_id = str(content.get("run_id") or metadata.get("run_id") or "").strip()
        if thread_id and run_id:
            return f"thread_run:{thread_id}:{run_id}"
        if snapshot_id:
            return f"snapshot:{snapshot_id}"
        if entity_scope:
            return f"scope:{entity_scope}"
        if thread_id:
            return f"thread:{thread_id}"
        if run_id:
            return f"run:{run_id}"
        return None

    def _candidate_sort_key(self, candidate: tuple[float, str, dict[str, Any]]) -> tuple[float, float, str]:
        score, recorded_at, _ = candidate
        return score, self._recorded_at_sort_value(recorded_at), recorded_at

    def _recorded_at_sort_value(self, recorded_at: str) -> float:
        parsed = self._parse_recorded_at(recorded_at)
        if parsed is None:
            return 0.0
        return parsed.timestamp()


__all__ = ["LocalMemoryAdapter"]
