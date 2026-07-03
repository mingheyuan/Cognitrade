from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research_system.runtime.core.id_utils import generate_ulid


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


def normalize_scope_token(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    return "".join(ch if ch.isalnum() or ch in {"-", "_", ":", "."} else "_" for ch in token)


def build_entity_scope(*, memory_scope: str, metadata: dict[str, Any], content: dict[str, Any], memory_type: str) -> str:
    if memory_scope == "chat_research":
        thread_id = normalize_scope_token(metadata.get("thread_id") or content.get("thread_id"))
        if thread_id:
            return f"chat_thread:{thread_id}"
        intent = normalize_scope_token(metadata.get("intent") or content.get("intent"))
        if intent:
            return f"chat_intent:{intent}"
        run_id = normalize_scope_token(metadata.get("run_id") or content.get("run_id"))
        if run_id:
            return f"chat_run:{run_id}"
        return "chat:global"
    benchmark_scope = normalize_scope_token(content.get("benchmark_scope") or metadata.get("benchmark_scope"))
    if benchmark_scope:
        return f"methodology:{memory_type}:{benchmark_scope}"
    proposal_id = normalize_scope_token(content.get("proposal_id") or metadata.get("proposal_id"))
    if proposal_id:
        return f"methodology:{memory_type}:proposal:{proposal_id}"
    return f"methodology:{memory_type}:global"


def build_snapshot_id(
    *,
    memory_scope: str,
    memory_type: str,
    metadata: dict[str, Any],
    content: dict[str, Any],
    entity_scope: str,
) -> str:
    thread_id = normalize_scope_token(metadata.get("thread_id") or content.get("thread_id"))
    run_id = normalize_scope_token(metadata.get("run_id") or content.get("run_id"))
    proposal_id = normalize_scope_token(content.get("proposal_id") or metadata.get("proposal_id"))
    benchmark_scope = normalize_scope_token(content.get("benchmark_scope") or metadata.get("benchmark_scope"))
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


def record_protocol_value(row: dict[str, Any], key: str) -> str:
    content = row.get("content") if isinstance(row.get("content"), dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    content_value = str((content or {}).get(key) or "").strip()
    if content_value:
        return content_value
    return str((metadata or {}).get(key) or "").strip()


def record_sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    recorded_at = str(row.get("recorded_at") or record_protocol_value(row, "recorded_at") or "")
    snapshot_id = record_protocol_value(row, "snapshot_id")
    memory_id = record_protocol_value(row, "memory_id")
    return recorded_at, snapshot_id, memory_id


def latest_rows_by_entity_scope(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_scope: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for row in rows:
        entity_scope = record_protocol_value(row, "entity_scope")
        if not entity_scope:
            passthrough.append(row)
            continue
        existing = latest_by_scope.get(entity_scope)
        if existing is None or record_sort_key(row) > record_sort_key(existing):
            latest_by_scope[entity_scope] = row
    ordered = sorted(latest_by_scope.values(), key=record_sort_key, reverse=True)
    passthrough.sort(key=record_sort_key, reverse=True)
    return [*ordered, *passthrough]
