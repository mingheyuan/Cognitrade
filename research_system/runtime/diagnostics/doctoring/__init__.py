from __future__ import annotations

from importlib import import_module

__all__ = [
    "build_config_file_checks",
    "build_doctor_checks",
    "build_doctor_payload",
    "build_environment_checks",
    "build_provider_checks",
    "build_qdrant_checks",
    "build_router_checks",
    "build_runs_presence_check",
    "build_status",
    "build_workspace_path_checks",
    "run_doctor",
]

_EXPORTS = {
    "build_config_file_checks": (
        "research_system.runtime.diagnostics.doctoring.doctor_workspace_checks",
        "build_config_file_checks",
    ),
    "build_doctor_checks": (
        "research_system.runtime.diagnostics.doctoring.doctor_checks",
        "build_doctor_checks",
    ),
    "build_doctor_payload": (
        "research_system.runtime.diagnostics.doctoring.doctor_checks",
        "build_doctor_payload",
    ),
    "build_environment_checks": (
        "research_system.runtime.diagnostics.doctoring.doctor_environment_checks",
        "build_environment_checks",
    ),
    "build_provider_checks": (
        "research_system.runtime.diagnostics.doctoring.doctor_provider_checks",
        "build_provider_checks",
    ),
    "build_qdrant_checks": (
        "research_system.runtime.diagnostics.doctoring.doctor_workspace_checks",
        "build_qdrant_checks",
    ),
    "build_router_checks": (
        "research_system.runtime.diagnostics.doctoring.doctor_router_checks",
        "build_router_checks",
    ),
    "build_runs_presence_check": (
        "research_system.runtime.diagnostics.doctoring.doctor_workspace_checks",
        "build_runs_presence_check",
    ),
    "build_status": (
        "research_system.runtime.diagnostics.doctoring.doctor",
        "build_status",
    ),
    "build_workspace_path_checks": (
        "research_system.runtime.diagnostics.doctoring.doctor_workspace_checks",
        "build_workspace_path_checks",
    ),
    "run_doctor": (
        "research_system.runtime.diagnostics.doctoring.doctor",
        "run_doctor",
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
