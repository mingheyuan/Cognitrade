from __future__ import annotations

from importlib import import_module

__all__ = [
    "artifacts",
    "distillation",
    "domain_pack",
    "execution_graph",
    "execution_runtime",
    "governance",
    "memory",
    "proposals",
    "protocol",
    "retrieval",
    "review",
    "runtime",
    "snapshots",
    "subagent_runtime_registration",
    "subagent_runtime_registration_hints",
    "subagents",
    "task_requests",
    "valuation",
]

_EXPORTS = {
    "artifacts": "research_system.schemas.artifacts",
    "distillation": "research_system.schemas.distillation",
    "domain_pack": "research_system.schemas.domain_pack",
    "execution_graph": "research_system.schemas.execution_graph",
    "execution_runtime": "research_system.schemas.execution_runtime",
    "governance": "research_system.schemas.governance",
    "memory": "research_system.schemas.memory",
    "proposals": "research_system.schemas.proposals",
    "protocol": "research_system.schemas.protocol",
    "retrieval": "research_system.schemas.retrieval",
    "review": "research_system.schemas.review",
    "runtime": "research_system.schemas.runtime",
    "snapshots": "research_system.schemas.snapshots",
    "subagent_runtime_registration": "research_system.schemas.subagent_runtime_registration",
    "subagent_runtime_registration_hints": "research_system.schemas.subagent_runtime_registration_hints",
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
