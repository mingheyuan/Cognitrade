from __future__ import annotations

CURRENT_THREAD_MEMORY = "current_thread"
SAME_INTENT_RECENT_MEMORY = "same_intent_recent_memory"
METHODOLOGY_MEMORY = "methodology_memory"
GLOBAL_MEMORY = "global_memory"
QDRANT_ARTIFACT_MEMORY = "qdrant_artifact"

MEMORY_RETRIEVAL_PRIORITY = (
    CURRENT_THREAD_MEMORY,
    SAME_INTENT_RECENT_MEMORY,
    METHODOLOGY_MEMORY,
    GLOBAL_MEMORY,
    QDRANT_ARTIFACT_MEMORY,
)

_MEMORY_RETRIEVAL_PRIORITY_RANK = {
    value: index
    for index, value in enumerate(MEMORY_RETRIEVAL_PRIORITY)
}


def memory_retrieval_priority_rank(value: str | None) -> int:
    normalized = str(value or "").strip()
    if not normalized:
        return len(MEMORY_RETRIEVAL_PRIORITY)
    return _MEMORY_RETRIEVAL_PRIORITY_RANK.get(normalized, len(MEMORY_RETRIEVAL_PRIORITY))


__all__ = [
    "CURRENT_THREAD_MEMORY",
    "GLOBAL_MEMORY",
    "MEMORY_RETRIEVAL_PRIORITY",
    "METHODOLOGY_MEMORY",
    "QDRANT_ARTIFACT_MEMORY",
    "SAME_INTENT_RECENT_MEMORY",
    "memory_retrieval_priority_rank",
]
