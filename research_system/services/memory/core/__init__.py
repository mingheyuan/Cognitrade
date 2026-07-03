from __future__ import annotations

from importlib import import_module

__all__ = [
    "CHAT_RESEARCH_MEMORY_TYPE",
    "CHAT_RESEARCH_SNAPSHOT_VERSION",
    "MEMORY_RECORD_SCHEMA_VERSION",
    "METHODOLOGY_MEMORY_TYPES",
    "METHODOLOGY_SNAPSHOT_VERSION",
    "MemoryService",
    "build_entity_scope",
    "build_memory_id",
    "build_memory_recorded_at",
    "build_snapshot_id",
    "latest_rows_by_entity_scope",
    "normalize_scope_token",
    "record_protocol_value",
    "record_sort_key",
]

_EXPORTS = {
    "CHAT_RESEARCH_MEMORY_TYPE": (
        "research_system.services.memory.core.protocol",
        "CHAT_RESEARCH_MEMORY_TYPE",
    ),
    "CHAT_RESEARCH_SNAPSHOT_VERSION": (
        "research_system.services.memory.core.protocol",
        "CHAT_RESEARCH_SNAPSHOT_VERSION",
    ),
    "MEMORY_RECORD_SCHEMA_VERSION": (
        "research_system.services.memory.core.protocol",
        "MEMORY_RECORD_SCHEMA_VERSION",
    ),
    "METHODOLOGY_MEMORY_TYPES": (
        "research_system.services.memory.core.protocol",
        "METHODOLOGY_MEMORY_TYPES",
    ),
    "METHODOLOGY_SNAPSHOT_VERSION": (
        "research_system.services.memory.core.protocol",
        "METHODOLOGY_SNAPSHOT_VERSION",
    ),
    "MemoryService": (
        "research_system.services.memory.core.service",
        "MemoryService",
    ),
    "build_entity_scope": (
        "research_system.services.memory.core.protocol",
        "build_entity_scope",
    ),
    "build_memory_id": (
        "research_system.services.memory.core.protocol",
        "build_memory_id",
    ),
    "build_memory_recorded_at": (
        "research_system.services.memory.core.protocol",
        "build_memory_recorded_at",
    ),
    "build_snapshot_id": (
        "research_system.services.memory.core.protocol",
        "build_snapshot_id",
    ),
    "latest_rows_by_entity_scope": (
        "research_system.services.memory.core.protocol",
        "latest_rows_by_entity_scope",
    ),
    "normalize_scope_token": (
        "research_system.services.memory.core.protocol",
        "normalize_scope_token",
    ),
    "record_protocol_value": (
        "research_system.services.memory.core.protocol",
        "record_protocol_value",
    ),
    "record_sort_key": (
        "research_system.services.memory.core.protocol",
        "record_sort_key",
    ),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
