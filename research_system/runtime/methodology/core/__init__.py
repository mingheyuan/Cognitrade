from __future__ import annotations

from importlib import import_module

__all__ = [
    "OrchestratorMethodologyBuilder",
    "OrchestratorMethodologyCompareSupport",
    "OrchestratorMethodologyEntryFactory",
    "OrchestratorMethodologySupport",
    "OrchestratorMethodologySymbolSupport",
]

_EXPORTS = {
    "OrchestratorMethodologyBuilder": (
        "research_system.runtime.methodology.core.builder",
        "OrchestratorMethodologyBuilder",
    ),
    "OrchestratorMethodologyCompareSupport": (
        "research_system.runtime.methodology.core.compare_support",
        "OrchestratorMethodologyCompareSupport",
    ),
    "OrchestratorMethodologyEntryFactory": (
        "research_system.runtime.methodology.core.entry_factory",
        "OrchestratorMethodologyEntryFactory",
    ),
    "OrchestratorMethodologySupport": (
        "research_system.runtime.methodology.core.support",
        "OrchestratorMethodologySupport",
    ),
    "OrchestratorMethodologySymbolSupport": (
        "research_system.runtime.methodology.core.symbol_support",
        "OrchestratorMethodologySymbolSupport",
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
