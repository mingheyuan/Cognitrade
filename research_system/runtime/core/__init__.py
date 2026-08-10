from __future__ import annotations

from importlib import import_module

__all__ = [
    "ArtifactBoard",
    "HumanApprovalStore",
    "generate_ulid",
]

_EXPORTS = {
    "ArtifactBoard": ("research_system.runtime.core.artifact_board", "ArtifactBoard"),
    "HumanApprovalStore": ("research_system.runtime.core.approvals", "HumanApprovalStore"),
    "generate_ulid": ("research_system.runtime.core.id_utils", "generate_ulid"),
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
