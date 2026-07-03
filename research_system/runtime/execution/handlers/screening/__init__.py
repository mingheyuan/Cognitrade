from __future__ import annotations

from importlib import import_module

__all__ = [
    "add_screening_candidates",
    "add_screening_scorecard",
    "collect_screening_inputs",
    "run_screener",
    "run_screening_enrichment",
]

_EXPORTS = {
    "add_screening_candidates": (
        "research_system.runtime.execution.handlers.screening.screening_entries",
        "add_screening_candidates",
    ),
    "add_screening_scorecard": (
        "research_system.runtime.execution.handlers.screening.screening_entries",
        "add_screening_scorecard",
    ),
    "collect_screening_inputs": (
        "research_system.runtime.execution.handlers.screening.screening_context",
        "collect_screening_inputs",
    ),
    "run_screener": (
        "research_system.runtime.execution.handlers.screening.screening",
        "run_screener",
    ),
    "run_screening_enrichment": (
        "research_system.runtime.execution.handlers.screening.screening_enrichment",
        "run_screening_enrichment",
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
