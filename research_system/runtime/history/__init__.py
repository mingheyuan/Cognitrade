from __future__ import annotations

from importlib import import_module

__all__ = [
    "RunTraceRowBuilder",
    "SessionHistory",
    "SessionHistoryStepBuilder",
    "SessionHistoryTraceRebuilder",
]

_EXPORTS = {
    "RunTraceRowBuilder": (
        "research_system.runtime.history.session.trace.run_trace_row_builder",
        "RunTraceRowBuilder",
    ),
    "SessionHistory": ("research_system.runtime.history.session.session_history", "SessionHistory"),
    "SessionHistoryStepBuilder": (
        "research_system.runtime.history.session.steps.step_builder",
        "SessionHistoryStepBuilder",
    ),
    "SessionHistoryTraceRebuilder": (
        "research_system.runtime.history.session.trace.trace_rebuilder",
        "SessionHistoryTraceRebuilder",
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
