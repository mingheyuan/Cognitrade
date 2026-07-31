from __future__ import annotations

from importlib import import_module

__all__ = [
    "CashflowResilienceService",
    "CompsService",
    "DCFService",
]

_EXPORTS = {
    "CashflowResilienceService": (
        "research_system.services.methodologies.valuation.cashflow_resilience_service",
        "CashflowResilienceService",
    ),
    "CompsService": ("research_system.services.methodologies.valuation.comps_service", "CompsService"),
    "DCFService": ("research_system.services.methodologies.valuation.dcf_service", "DCFService"),
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
