from __future__ import annotations

from importlib import import_module

__all__ = [
    "MemoryServiceWriteSupport",
    "MethodologyMemoryWriter",
]

_EXPORTS = {
    "MemoryServiceWriteSupport": (
        "research_system.services.memory.write.write_support",
        "MemoryServiceWriteSupport",
    ),
    "MethodologyMemoryWriter": (
        "research_system.services.memory.write.methodology_writer",
        "MethodologyMemoryWriter",
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
