from __future__ import annotations

from importlib import import_module

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "adapters",
    "runtime",
    "schemas",
    "services",
    "subagents",
]

_EXPORTS = {
    "adapters": "research_system.adapters",
    "runtime": "research_system.runtime",
    "schemas": "research_system.schemas",
    "services": "research_system.services",
    "subagents": "research_system.subagents",
}


def __getattr__(name: str):
    if name == "__version__":
        return __version__
    try:
        module_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = import_module(module_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
