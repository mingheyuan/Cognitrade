from __future__ import annotations

from importlib import import_module

__all__ = [
    "OrchestratorProposalCollectSupport",
    "OrchestratorProposalEntryFactory",
    "OrchestratorProposalReviewPipeline",
    "OrchestratorProposalSupport",
    "OrchestratorProposalTransitionSupport",
]

_EXPORTS = {
    "OrchestratorProposalCollectSupport": (
        "research_system.runtime.proposals.workflow.collect_support",
        "OrchestratorProposalCollectSupport",
    ),
    "OrchestratorProposalEntryFactory": (
        "research_system.runtime.proposals.workflow.entry_factory",
        "OrchestratorProposalEntryFactory",
    ),
    "OrchestratorProposalReviewPipeline": (
        "research_system.runtime.proposals.workflow.review_pipeline",
        "OrchestratorProposalReviewPipeline",
    ),
    "OrchestratorProposalSupport": (
        "research_system.runtime.proposals.workflow.support",
        "OrchestratorProposalSupport",
    ),
    "OrchestratorProposalTransitionSupport": (
        "research_system.runtime.proposals.workflow.transition_support",
        "OrchestratorProposalTransitionSupport",
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
