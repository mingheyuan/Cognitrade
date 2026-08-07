from __future__ import annotations

from importlib import import_module

__all__ = [
    "artifacts",
    "domain_pack",
    "execution_graph",
    "execution_runtime",
    "events",
    "governance",
    "memory",
    "protocol",
    "runtime",
    "snapshots",
    "subagents",
    "tools",
]

_EXPORTS = {
    "artifacts": "research_system.schemas.artifacts",
    "domain_pack": "research_system.schemas.domain_pack",
    "execution_graph": "research_system.schemas.execution_graph",
    "execution_runtime": "research_system.schemas.execution_runtime",
    "events": "research_system.schemas.events",
    "governance": "research_system.schemas.governance",
    "memory": "research_system.schemas.memory",
    "protocol": "research_system.schemas.protocol",
    "runtime": "research_system.schemas.runtime",
    "snapshots": "research_system.schemas.snapshots",
    "subagents": "research_system.schemas.subagents",
    "tools": "research_system.schemas.tools",
}


def __getattr__(name: str) -> object:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return import_module(_EXPORTS[name])


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
