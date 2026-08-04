from __future__ import annotations

from importlib import import_module

__all__ = [
    "ProviderHealthService",
    "UZI_FETCHER_DIMENSIONS",
    "build_runtime_status",
    "run_uzi_provider_smoke",
]

_EXPORTS = {
    "ProviderHealthService": (
        "research_system.runtime.diagnostics.status.provider_health",
        "ProviderHealthService",
    ),
    "UZI_FETCHER_DIMENSIONS": (
        "research_system.runtime.diagnostics.status.uzi_provider_smoke",
        "UZI_FETCHER_DIMENSIONS",
    ),
    "build_runtime_status": (
        "research_system.runtime.diagnostics.status.status_runtime",
        "build_runtime_status",
    ),
    "run_uzi_provider_smoke": (
        "research_system.runtime.diagnostics.status.uzi_provider_smoke",
        "run_uzi_provider_smoke",
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
