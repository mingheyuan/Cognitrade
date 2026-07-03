from __future__ import annotations

from importlib import import_module

__all__ = [
    "ProviderHealthService",
    "build_runtime_status",
]

_EXPORTS = {
    "ProviderHealthService": (
        "research_system.runtime.diagnostics.status.provider_health",
        "ProviderHealthService",
    ),
    "build_runtime_status": (
        "research_system.runtime.diagnostics.status.status_runtime",
        "build_runtime_status",
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
