from __future__ import annotations

from importlib import import_module

__all__ = [
    "CHAT_RESEARCH_MEMORY_TYPE",
    "CHAT_RESEARCH_SNAPSHOT_VERSION",
    "MemoryServiceCore",
    "MEMORY_RECORD_SCHEMA_VERSION",
    "METHODOLOGY_MEMORY_TYPES",
    "METHODOLOGY_SNAPSHOT_VERSION",
    "MemoryServiceBaseQuerySupport",
    "MemoryServiceChatQuerySupport",
    "MemoryServiceMethodologyQuerySupport",
    "MemoryServiceQuerySupport",
    "MemoryService",
    "MemoryServiceWriteSupport",
    "MethodologyMemoryWriter",
    "build_memory_id",
    "build_memory_recorded_at",
]

_EXPORTS = {
    "CHAT_RESEARCH_MEMORY_TYPE": ("research_system.services.memory.core.protocol", "CHAT_RESEARCH_MEMORY_TYPE"),
    "CHAT_RESEARCH_SNAPSHOT_VERSION": (
        "research_system.services.memory.core.protocol",
        "CHAT_RESEARCH_SNAPSHOT_VERSION",
    ),
    "MemoryServiceCore": ("research_system.services.memory.core", "MemoryService"),
    "MEMORY_RECORD_SCHEMA_VERSION": (
        "research_system.services.memory.core.protocol",
        "MEMORY_RECORD_SCHEMA_VERSION",
    ),
    "METHODOLOGY_MEMORY_TYPES": ("research_system.services.memory.core.protocol", "METHODOLOGY_MEMORY_TYPES"),
    "METHODOLOGY_SNAPSHOT_VERSION": (
        "research_system.services.memory.core.protocol",
        "METHODOLOGY_SNAPSHOT_VERSION",
    ),
    "MemoryServiceBaseQuerySupport": (
        "research_system.services.memory.query.base_query_support",
        "MemoryServiceBaseQuerySupport",
    ),
    "MemoryServiceChatQuerySupport": (
        "research_system.services.memory.query.chat_query_support",
        "MemoryServiceChatQuerySupport",
    ),
    "MemoryServiceMethodologyQuerySupport": (
        "research_system.services.memory.query.methodology_query_support",
        "MemoryServiceMethodologyQuerySupport",
    ),
    "MemoryServiceQuerySupport": (
        "research_system.services.memory.query.query_support",
        "MemoryServiceQuerySupport",
    ),
    "MemoryService": ("research_system.services.memory.core.service", "MemoryService"),
    "MemoryServiceWriteSupport": (
        "research_system.services.memory.write.write_support",
        "MemoryServiceWriteSupport",
    ),
    "MethodologyMemoryWriter": (
        "research_system.services.memory.write.methodology_writer",
        "MethodologyMemoryWriter",
    ),
    "build_memory_id": ("research_system.services.memory.core.protocol", "build_memory_id"),
    "build_memory_recorded_at": ("research_system.services.memory.core.protocol", "build_memory_recorded_at"),
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
