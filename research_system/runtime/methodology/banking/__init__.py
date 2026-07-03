from __future__ import annotations

from importlib import import_module

__all__ = [
    "OrchestratorMethodologyBankHighlightSupport",
    "OrchestratorMethodologyBankMetricSupport",
    "OrchestratorMethodologyBankScorecardSupport",
]

_EXPORTS = {
    "OrchestratorMethodologyBankHighlightSupport": (
        "research_system.runtime.methodology.banking.bank_highlight_support",
        "OrchestratorMethodologyBankHighlightSupport",
    ),
    "OrchestratorMethodologyBankMetricSupport": (
        "research_system.runtime.methodology.banking.bank_metric_support",
        "OrchestratorMethodologyBankMetricSupport",
    ),
    "OrchestratorMethodologyBankScorecardSupport": (
        "research_system.runtime.methodology.banking.bank_scorecard",
        "OrchestratorMethodologyBankScorecardSupport",
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
