from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from research_system.runtime.jsonl import read_jsonl
from research_system.runtime.jsonl import append_jsonl
from research_system.runtime.settings import Settings

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
_CHAT_RESEARCH_MEMORY_TYPE = "chat_research_summary"
_MEMORY_RECORD_SCHEMA_VERSION = "memory_record_v2"
_CHAT_RESEARCH_SNAPSHOT_VERSION = "chat_research_v2"
_METHODOLOGY_SNAPSHOT_VERSION = "methodology_v2"
_METHODOLOGY_MEMORY_TYPES = {
    "methodology_summary",
    "dimension_experience",
    "error_pattern",
    "user_preference",
}


class Mem0Adapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = bool(settings.mem0_api_key and settings.mem0_base_url)
        self.export_path = settings.memory_root / "exports" / "mem0_exports.jsonl"
        self.base_url = str(settings.mem0_base_url or "").rstrip("/")
        self.api_key = str(settings.mem0_api_key or "").strip()

    def write(self, *, memory_type: str, content: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_content, normalized_metadata, recorded_at = self._normalize_record_payload(
            content=content,
            metadata=metadata or {},
        )
        record = {
            "memory_type": memory_type,
            "content": normalized_content,
            "metadata": normalized_metadata,
            "sink": "local_export",
            "recorded_at": recorded_at,
        }
        if self.enabled:
            remote_record = self._remote_write(memory_type=memory_type, content=normalized_content, metadata=normalized_metadata)
            if remote_record is not None:
                record["sink"] = "mem0_api"
                record["remote"] = remote_record
        self.export_path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(self.export_path, record)
        return record

    def read_recent(self, *, memory_type: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        if self.enabled:
            remote_rows = self._remote_read_recent(memory_type=memory_type, limit=limit)
            if remote_rows is not None:
                return remote_rows
        rows = read_jsonl(self.export_path)
        if memory_type:
            rows = [row for row in rows if str(row.get("memory_type") or "") == memory_type]
        if limit <= 0:
            return []
        rows.reverse()
        normalized_rows: list[dict[str, Any]] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            content = row.get("content") if isinstance(row.get("content"), dict) else {}
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            content, metadata, recorded_at = self._normalize_record_payload(content=content, metadata=metadata)
            normalized_row = dict(row)
            normalized_row["content"] = content
            normalized_row["metadata"] = metadata
            normalized_row["recorded_at"] = recorded_at
            normalized_rows.append(normalized_row)
        return normalized_rows

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
        filters = dict(metadata_filters or {})
        if self.enabled:
            remote_rows = self._remote_search(
                query=normalized_query,
                memory_types=memory_types,
                limit=limit,
                metadata_filters=filters,
            )
            if remote_rows is not None:
                return remote_rows
        rows = read_jsonl(self.export_path)
        return self._score_and_filter_rows(
            rows=rows,
            query=normalized_query,
            memory_types=memory_types,
            limit=limit,
            metadata_filters=filters,
        )

    def _remote_write(self, *, memory_type: str, content: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any] | None:
        payload = {
            "messages": [
                {
                    "role": "system",
                    "content": f"memory_type={memory_type}",
                },
                {
                    "role": "user",
                    "content": json.dumps(content, ensure_ascii=False),
                },
            ],
            "metadata": {
                "memory_type": memory_type,
                **metadata,
            },
            "user_id": self._resolve_user_id(content=content, metadata=metadata),
            "agent_id": "cognitrade",
            "run_id": str(content.get("run_id") or metadata.get("run_id") or "").strip() or None,
        }
        return self._request_json("POST", "/v1/memories/", payload)

    def _remote_read_recent(self, *, memory_type: str | None, limit: int) -> list[dict[str, Any]] | None:
        payload = {
            "query": f"memory_type={memory_type}" if memory_type else "memory_type:*",
            "limit": limit,
            "filters": {"memory_type": memory_type} if memory_type else {},
        }
        response = self._request_json("POST", "/v1/memories/search/", payload)
        if not isinstance(response, dict):
            return None
        items = response.get("results") or response.get("memories") or response.get("data") or []
        if not isinstance(items, list):
            return None
        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            memory_type_value = str(metadata.get("memory_type") or memory_type or "").strip()
            raw_memory = item.get("memory") or item.get("text") or item.get("content")
            content = self._coerce_remote_content(raw_memory)
            metadata = self._hydrate_protocol_metadata(
                memory_type=memory_type_value,
                content=content,
                metadata=metadata,
            )
            content, metadata, recorded_at = self._normalize_record_payload(content=content, metadata=metadata)
            rows.append(
                {
                    "memory_type": memory_type_value,
                    "content": content,
                    "metadata": metadata,
                    "sink": "mem0_api",
                    "recorded_at": recorded_at,
                    "remote": item,
                }
            )
        return rows

    def _remote_search(
        self,
        *,
        query: str,
        memory_types: list[str] | None,
        limit: int,
        metadata_filters: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        filters = dict(metadata_filters)
        if memory_types:
            if len(memory_types) == 1:
                filters["memory_type"] = memory_types[0]
            else:
                filters["memory_type"] = list(memory_types)
        payload = {
            "query": query,
            "limit": limit,
            "filters": filters,
        }
        response = self._request_json("POST", "/v1/memories/search/", payload)
        if not isinstance(response, dict):
            return None
        items = response.get("results") or response.get("memories") or response.get("data") or []
        if not isinstance(items, list):
            return None
        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            memory_type_value = str(metadata.get("memory_type") or "").strip()
            if not memory_type_value and memory_types:
                memory_type_value = str(memory_types[0] or "").strip()
            raw_memory = item.get("memory") or item.get("text") or item.get("content")
            content = self._coerce_remote_content(raw_memory)
            if memory_type_value and "memory_type" not in metadata:
                metadata = {"memory_type": memory_type_value, **metadata}
            metadata = self._hydrate_protocol_metadata(
                memory_type=memory_type_value,
                content=content,
                metadata=metadata,
            )
            content, metadata, recorded_at = self._normalize_record_payload(content=content, metadata=metadata)
            rows.append(
                {
                    "memory_type": memory_type_value,
                    "content": content,
                    "metadata": metadata,
                    "sink": "mem0_api",
                    "recorded_at": recorded_at,
                    "remote": item,
                }
            )
        return self._score_and_filter_rows(
            rows=rows,
            query=query,
            memory_types=memory_types,
            limit=limit,
            metadata_filters=metadata_filters,
        )

    def _request_json(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.base_url or not self.api_key:
            return None
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            urljoin(f"{self.base_url}/", path.lstrip("/")),
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Token {self.api_key}",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                text = response.read().decode(charset)
                return json.loads(text) if text.strip() else {}
        except (HTTPError, URLError, TimeoutError, ValueError):
            return None

    def _coerce_remote_content(self, raw_memory: object) -> dict[str, Any]:
        if isinstance(raw_memory, dict):
            return raw_memory
        text = str(raw_memory or "").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else {"text": text}
        except json.JSONDecodeError:
            return {"text": text}

    def _resolve_user_id(self, *, content: dict[str, Any], metadata: dict[str, Any]) -> str:
        thread_id = str(content.get("thread_id") or metadata.get("thread_id") or "").strip()
        if thread_id:
            return thread_id
        return "cognitrade"

    def _normalize_record_payload(
        self,
        *,
        content: dict[str, Any],
        metadata: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str | None]:
        normalized_content = dict(content or {})
        normalized_metadata = dict(metadata or {})
        protocol_keys = (
            "memory_id",
            "schema_version",
            "snapshot_version",
            "snapshot_id",
            "memory_scope",
            "entity_scope",
            "recorded_at",
        )
        for key in protocol_keys:
            content_value = normalized_content.get(key)
            metadata_value = normalized_metadata.get(key)
            if content_value in (None, "") and metadata_value not in (None, ""):
                normalized_content[key] = metadata_value
            elif metadata_value in (None, "") and content_value not in (None, ""):
                normalized_metadata[key] = content_value
        recorded_at = str(
            normalized_content.get("recorded_at")
            or normalized_metadata.get("recorded_at")
            or ""
        ).strip() or None
        if recorded_at is not None:
            normalized_content["recorded_at"] = recorded_at
            normalized_metadata["recorded_at"] = recorded_at
        return normalized_content, normalized_metadata, recorded_at

    def _hydrate_protocol_metadata(
        self,
        *,
        memory_type: str,
        content: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(metadata or {})
        if not memory_type:
            return normalized
        if "memory_scope" not in normalized:
            if memory_type == _CHAT_RESEARCH_MEMORY_TYPE:
                normalized["memory_scope"] = "chat_research"
            elif memory_type in _METHODOLOGY_MEMORY_TYPES:
                normalized["memory_scope"] = "methodology"
        if "schema_version" not in normalized:
            normalized["schema_version"] = _MEMORY_RECORD_SCHEMA_VERSION
        if "snapshot_version" not in normalized:
            if str(normalized.get("memory_scope") or "") == "chat_research":
                normalized["snapshot_version"] = _CHAT_RESEARCH_SNAPSHOT_VERSION
            elif str(normalized.get("memory_scope") or "") == "methodology":
                normalized["snapshot_version"] = _METHODOLOGY_SNAPSHOT_VERSION
        if "entity_scope" not in normalized:
            thread_id = str(content.get("thread_id") or normalized.get("thread_id") or "").strip()
            intent = str(content.get("intent") or normalized.get("intent") or "").strip()
            run_id = str(content.get("run_id") or normalized.get("run_id") or "").strip()
            benchmark_scope = str(content.get("benchmark_scope") or normalized.get("benchmark_scope") or "").strip()
            proposal_id = str(content.get("proposal_id") or normalized.get("proposal_id") or "").strip()
            if str(normalized.get("memory_scope") or "") == "chat_research":
                if thread_id:
                    normalized["entity_scope"] = f"chat_thread:{thread_id}"
                elif intent:
                    normalized["entity_scope"] = f"chat_intent:{intent}"
                elif run_id:
                    normalized["entity_scope"] = f"chat_run:{run_id}"
                else:
                    normalized["entity_scope"] = "chat:global"
            elif str(normalized.get("memory_scope") or "") == "methodology":
                if benchmark_scope:
                    normalized["entity_scope"] = f"methodology:{memory_type}:{benchmark_scope}"
                elif proposal_id:
                    normalized["entity_scope"] = f"methodology:{memory_type}:proposal:{proposal_id}"
                else:
                    normalized["entity_scope"] = f"methodology:{memory_type}:global"
        if "snapshot_id" not in normalized:
            thread_id = str(content.get("thread_id") or normalized.get("thread_id") or "").strip()
            run_id = str(content.get("run_id") or normalized.get("run_id") or "").strip()
            benchmark_scope = str(content.get("benchmark_scope") or normalized.get("benchmark_scope") or "").strip()
            proposal_id = str(content.get("proposal_id") or normalized.get("proposal_id") or "").strip()
            if str(normalized.get("memory_scope") or "") == "chat_research":
                if thread_id and run_id:
                    normalized["snapshot_id"] = f"chat_snapshot:{thread_id}:{run_id}"
                elif run_id:
                    normalized["snapshot_id"] = f"chat_snapshot:run:{run_id}"
            elif proposal_id:
                normalized["snapshot_id"] = f"methodology_snapshot:proposal:{proposal_id}"
            elif benchmark_scope:
                normalized["snapshot_id"] = f"methodology_snapshot:{memory_type}:{benchmark_scope}"
        return normalized

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
