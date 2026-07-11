from __future__ import annotations

from importlib import import_module

__all__ = [
    "CHAT_RESEARCH_MEMORY_TYPE",
    "CHAT_RESEARCH_SNAPSHOT_VERSION",
    "CURRENT_THREAD_MEMORY",
    "GLOBAL_MEMORY",
    "MemoryServiceCore",
    "MEMORY_RECORD_SCHEMA_VERSION",
    "MEMORY_RETRIEVAL_PRIORITY",
    "METHODOLOGY_MEMORY",
    "METHODOLOGY_MEMORY_TYPES",
    "METHODOLOGY_SNAPSHOT_VERSION",
    "MemoryServiceBaseQuerySupport",
    "MemoryServiceChatQuerySupport",
    "MemoryServiceMethodologyQuerySupport",
    "MemoryServiceQuerySupport",
    "MemoryService",
    "LayeredMemoryStore",
    "MemoryExpirationScheduler",
    "MemoryRetrievalBackend",
    "MemoryPromotionError",
    "MemoryServiceWriteSupport",
    "MethodologyMemoryWriter",
    "QDRANT_ARTIFACT_MEMORY",
    "SAME_INTENT_RECENT_MEMORY",
    "build_memory_id",
    "build_memory_recorded_at",
    "memory_retrieval_priority_rank",
]

_EXPORTS = {
    "CHAT_RESEARCH_MEMORY_TYPE": ("research_system.services.memory.core.protocol", "CHAT_RESEARCH_MEMORY_TYPE"),
    "CHAT_RESEARCH_SNAPSHOT_VERSION": (
        "research_system.services.memory.core.protocol",
        "CHAT_RESEARCH_SNAPSHOT_VERSION",
    ),
    "CURRENT_THREAD_MEMORY": (
        "research_system.services.memory.core.retrieval_priority",
        "CURRENT_THREAD_MEMORY",
    ),
    "GLOBAL_MEMORY": (
        "research_system.services.memory.core.retrieval_priority",
        "GLOBAL_MEMORY",
    ),
    "MemoryServiceCore": ("research_system.services.memory.core", "MemoryService"),
    "MEMORY_RECORD_SCHEMA_VERSION": (
        "research_system.services.memory.core.protocol",
        "MEMORY_RECORD_SCHEMA_VERSION",
    ),
    "MEMORY_RETRIEVAL_PRIORITY": (
        "research_system.services.memory.core.retrieval_priority",
        "MEMORY_RETRIEVAL_PRIORITY",
    ),
    "METHODOLOGY_MEMORY": (
        "research_system.services.memory.core.retrieval_priority",
        "METHODOLOGY_MEMORY",
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
    "LayeredMemoryStore": ("research_system.services.memory.layers", "LayeredMemoryStore"),
    "MemoryExpirationScheduler": (
        "research_system.services.memory.maintenance",
        "MemoryExpirationScheduler",
    ),
    "MemoryRetrievalBackend": ("research_system.services.memory.layers", "MemoryRetrievalBackend"),
    "MemoryPromotionError": ("research_system.services.memory.layers", "MemoryPromotionError"),
    "MemoryServiceWriteSupport": (
        "research_system.services.memory.write.write_support",
        "MemoryServiceWriteSupport",
    ),
    "MethodologyMemoryWriter": (
        "research_system.services.memory.write.methodology_writer",
        "MethodologyMemoryWriter",
    ),
    "QDRANT_ARTIFACT_MEMORY": (
        "research_system.services.memory.core.retrieval_priority",
        "QDRANT_ARTIFACT_MEMORY",
    ),
    "SAME_INTENT_RECENT_MEMORY": (
        "research_system.services.memory.core.retrieval_priority",
        "SAME_INTENT_RECENT_MEMORY",
    ),
    "build_memory_id": ("research_system.services.memory.core.protocol", "build_memory_id"),
    "build_memory_recorded_at": ("research_system.services.memory.core.protocol", "build_memory_recorded_at"),
    "memory_retrieval_priority_rank": (
        "research_system.services.memory.core.retrieval_priority",
        "memory_retrieval_priority_rank",
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
