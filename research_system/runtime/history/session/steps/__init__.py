from __future__ import annotations

from importlib import import_module

__all__ = [
    "SessionHistoryStepBuilder",
    "SessionHistoryStepRows",
    "SessionHistorySupervisorState",
]

_EXPORTS = {
    "SessionHistoryStepBuilder": (
        "research_system.runtime.history.session.steps.step_builder",
        "SessionHistoryStepBuilder",
    ),
    "SessionHistoryStepRows": (
        "research_system.runtime.history.session.steps.step_rows",
        "SessionHistoryStepRows",
    ),
    "SessionHistorySupervisorState": (
        "research_system.runtime.history.session.steps.supervisor_state",
        "SessionHistorySupervisorState",
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
