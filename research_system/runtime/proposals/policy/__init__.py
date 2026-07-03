from __future__ import annotations

from importlib import import_module

__all__ = [
    "OrchestratorProposalApprovalPolicy",
    "OrchestratorProposalAutoApprovalSupport",
    "OrchestratorProposalMemoryGateSupport",
    "OrchestratorProposalTargetSupport",
]

_EXPORTS = {
    "OrchestratorProposalApprovalPolicy": (
        "research_system.runtime.proposals.policy.approval_policy",
        "OrchestratorProposalApprovalPolicy",
    ),
    "OrchestratorProposalAutoApprovalSupport": (
        "research_system.runtime.proposals.policy.auto_approval_support",
        "OrchestratorProposalAutoApprovalSupport",
    ),
    "OrchestratorProposalMemoryGateSupport": (
        "research_system.runtime.proposals.policy.memory_gate_support",
        "OrchestratorProposalMemoryGateSupport",
    ),
    "OrchestratorProposalTargetSupport": (
        "research_system.runtime.proposals.policy.target_support",
        "OrchestratorProposalTargetSupport",
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
