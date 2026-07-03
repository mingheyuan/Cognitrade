from __future__ import annotations

from importlib import import_module

__all__ = [
    "ProposalGenerator",
    "ProposalGeneratorDimensionSupport",
    "ProposalGeneratorReviewSupport",
    "ProposalGeneratorValuationSupport",
]

_EXPORTS = {
    "ProposalGenerator": (
        "research_system.services.proposals.generator.proposal_generator",
        "ProposalGenerator",
    ),
    "ProposalGeneratorDimensionSupport": (
        "research_system.services.proposals.generator.proposal_generator_dimension_support",
        "ProposalGeneratorDimensionSupport",
    ),
    "ProposalGeneratorReviewSupport": (
        "research_system.services.proposals.generator.proposal_generator_review_support",
        "ProposalGeneratorReviewSupport",
    ),
    "ProposalGeneratorValuationSupport": (
        "research_system.services.proposals.generator.proposal_generator_valuation_support",
        "ProposalGeneratorValuationSupport",
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
