from __future__ import annotations

from importlib import import_module

__all__ = [
    "SubagentCatalogMetadata",
    "SubagentDefinition",
    "SubagentRuntimePolicy",
    "SupportsSubagent",
    "SubagentRegistry",
    "base",
    "collector",
    "critic_review",
    "distillation",
    "planning",
    "reporting",
    "retrieval",
]

_EXPORTS = {
    "SubagentCatalogMetadata": ("research_system.subagents.base", "SubagentCatalogMetadata"),
    "SubagentDefinition": ("research_system.subagents.base", "SubagentDefinition"),
    "SubagentRuntimePolicy": ("research_system.subagents.base", "SubagentRuntimePolicy"),
    "SupportsSubagent": ("research_system.subagents.base", "SupportsSubagent"),
    "SubagentRegistry": ("research_system.subagents.registry", "SubagentRegistry"),
    "base": "research_system.subagents.base",
    "collector": "research_system.subagents.collector",
    "critic_review": "research_system.subagents.critic_review",
    "distillation": "research_system.subagents.distillation",
    "planning": "research_system.subagents.planning",
    "reporting": "research_system.subagents.reporting",
    "retrieval": "research_system.subagents.retrieval",
}


def __getattr__(name: str):
    try:
        target = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    if isinstance(target, tuple):
        module_name, attr_name = target
        value = getattr(import_module(module_name), attr_name)
    else:
        value = import_module(target)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
