from __future__ import annotations

from importlib import import_module

__all__ = [
    "distillation",
    "planning",
    "proposals",
    "protocol",
    "retrieval",
    "review",
    "runtime",
    "snapshots",
    "subagent_runtime_registration",
    "subagents",
    "task_requests",
    "valuation",
]

_EXPORTS = {
    "distillation": "research_system.schemas.distillation",
    "planning": "research_system.schemas.planning",
    "proposals": "research_system.schemas.proposals",
    "protocol": "research_system.schemas.protocol",
    "retrieval": "research_system.schemas.retrieval",
    "review": "research_system.schemas.review",
    "runtime": "research_system.schemas.runtime",
    "snapshots": "research_system.schemas.snapshots",
    "subagent_runtime_registration": "research_system.schemas.subagent_runtime_registration",
    "subagents": "research_system.schemas.subagents",
    "task_requests": "research_system.schemas.task_requests",
    "valuation": "research_system.schemas.valuation",
}


def __getattr__(name: str) -> object:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return import_module(_EXPORTS[name])


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
