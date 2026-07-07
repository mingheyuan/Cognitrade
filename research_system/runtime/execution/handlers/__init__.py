from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "OrchestratorAgentHandlers": (
        "research_system.runtime.execution.handlers.registry",
        "OrchestratorAgentHandlers",
    ),
    "run_collector": ("research_system.runtime.execution.handlers.dataflow.data", "run_collector"),
    "run_retriever": ("research_system.runtime.execution.handlers.dataflow.data", "run_retriever"),
    "run_screener": ("research_system.runtime.execution.handlers.screening.screening", "run_screener"),
    "run_distiller": ("research_system.runtime.execution.handlers.distillation.distillation", "run_distiller"),
    "run_critic": ("research_system.runtime.execution.handlers.distillation.distillation", "run_critic"),
    "run_reviewer": ("research_system.runtime.execution.handlers.reviewing.review", "run_reviewer"),
    "run_screen_reviewer": (
        "research_system.runtime.execution.handlers.reviewing.review",
        "run_screen_reviewer",
    ),
    "run_reporter": ("research_system.runtime.execution.handlers.reporting", "run_reporter"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
