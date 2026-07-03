from __future__ import annotations

from importlib import import_module

__all__ = [
    "add_critic_outputs",
    "add_distillation_outputs",
    "collect_critic_inputs",
    "collect_distiller_source_records",
    "run_critic",
    "run_distiller",
]

_EXPORTS = {
    "add_critic_outputs": (
        "research_system.runtime.execution.handlers.distillation.distillation_entries",
        "add_critic_outputs",
    ),
    "add_distillation_outputs": (
        "research_system.runtime.execution.handlers.distillation.distillation_entries",
        "add_distillation_outputs",
    ),
    "collect_critic_inputs": (
        "research_system.runtime.execution.handlers.distillation.distillation_context",
        "collect_critic_inputs",
    ),
    "collect_distiller_source_records": (
        "research_system.runtime.execution.handlers.distillation.distillation_context",
        "collect_distiller_source_records",
    ),
    "run_critic": (
        "research_system.runtime.execution.handlers.distillation.distillation",
        "run_critic",
    ),
    "run_distiller": (
        "research_system.runtime.execution.handlers.distillation.distillation",
        "run_distiller",
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
