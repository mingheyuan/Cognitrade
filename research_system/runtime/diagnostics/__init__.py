from __future__ import annotations

from importlib import import_module

__all__ = [
    "ProviderHealthService",
    "build_doctor_checks",
    "build_doctor_payload",
    "build_runtime_status",
    "build_status",
    "run_doctor",
]

_EXPORTS = {
    "ProviderHealthService": (
        "research_system.runtime.diagnostics.status.provider_health",
        "ProviderHealthService",
    ),
    "build_doctor_checks": (
        "research_system.runtime.diagnostics.doctoring.doctor_checks",
        "build_doctor_checks",
    ),
    "build_doctor_payload": (
        "research_system.runtime.diagnostics.doctoring.doctor_checks",
        "build_doctor_payload",
    ),
    "build_runtime_status": (
        "research_system.runtime.diagnostics.status.status_runtime",
        "build_runtime_status",
    ),
    "build_status": ("research_system.runtime.diagnostics.doctoring.doctor", "build_status"),
    "run_doctor": ("research_system.runtime.diagnostics.doctoring.doctor", "run_doctor"),
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
