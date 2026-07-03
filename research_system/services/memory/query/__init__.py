from __future__ import annotations

from importlib import import_module

__all__ = [
    "MemoryServiceBaseQuerySupport",
    "MemoryServiceChatQuerySupport",
    "MemoryServiceMethodologyQuerySupport",
    "MemoryServiceQuerySupport",
]

_EXPORTS = {
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
