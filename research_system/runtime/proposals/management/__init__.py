from __future__ import annotations

from importlib import import_module

__all__ = [
    "OrchestratorProposalAdminSupport",
    "ProposalStore",
]

_EXPORTS = {
    "OrchestratorProposalAdminSupport": (
        "research_system.runtime.proposals.management.admin_support",
        "OrchestratorProposalAdminSupport",
    ),
    "ProposalStore": (
        "research_system.runtime.proposals.management.proposal_store",
        "ProposalStore",
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
