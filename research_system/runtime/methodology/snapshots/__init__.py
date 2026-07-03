from __future__ import annotations

from importlib import import_module

__all__ = [
    "OrchestratorMethodologySnapshotCandidateSupport",
    "OrchestratorMethodologySnapshotFields",
    "OrchestratorMethodologySnapshotGrouping",
    "OrchestratorMethodologySnapshotSupport",
]

_EXPORTS = {
    "OrchestratorMethodologySnapshotCandidateSupport": (
        "research_system.runtime.methodology.snapshots.snapshot_candidate_support",
        "OrchestratorMethodologySnapshotCandidateSupport",
    ),
    "OrchestratorMethodologySnapshotFields": (
        "research_system.runtime.methodology.snapshots.snapshot_fields",
        "OrchestratorMethodologySnapshotFields",
    ),
    "OrchestratorMethodologySnapshotGrouping": (
        "research_system.runtime.methodology.snapshots.snapshot_grouping",
        "OrchestratorMethodologySnapshotGrouping",
    ),
    "OrchestratorMethodologySnapshotSupport": (
        "research_system.runtime.methodology.snapshots.snapshot_support",
        "OrchestratorMethodologySnapshotSupport",
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
