from __future__ import annotations

from importlib import import_module

__all__ = [
    "MethodologyEvolver",
    "MethodologyEvolverAggregation",
    "MethodologyEvolverDimensionAggregation",
    "MethodologyEvolverDimensionCandidates",
    "MethodologyEvolverExtraction",
    "MethodologyEvolverMemoryMatching",
    "MethodologyEvolverMemorySummary",
    "MethodologyEvolverMemorySupport",
    "MethodologyEvolverProtocolSupport",
    "MethodologyEvolverReviewAggregation",
    "MethodologyEvolverReviewCandidates",
    "MethodologyEvolverValuationAggregation",
    "MethodologyEvolverValuationCandidates",
]

_EXPORTS = {
    "MethodologyEvolver": ("research_system.services.methodologies.evolver.methodology_evolver", "MethodologyEvolver"),
    "MethodologyEvolverAggregation": (
        "research_system.services.methodologies.evolver.methodology_evolver_aggregation",
        "MethodologyEvolverAggregation",
    ),
    "MethodologyEvolverDimensionAggregation": (
        "research_system.services.methodologies.evolver.methodology_evolver_dimension_aggregation",
        "MethodologyEvolverDimensionAggregation",
    ),
    "MethodologyEvolverDimensionCandidates": (
        "research_system.services.methodologies.evolver.methodology_evolver_dimension_candidates",
        "MethodologyEvolverDimensionCandidates",
    ),
    "MethodologyEvolverExtraction": (
        "research_system.services.methodologies.evolver.methodology_evolver_extraction",
        "MethodologyEvolverExtraction",
    ),
    "MethodologyEvolverMemoryMatching": (
        "research_system.services.methodologies.evolver.methodology_evolver_memory_matching",
        "MethodologyEvolverMemoryMatching",
    ),
    "MethodologyEvolverMemorySummary": (
        "research_system.services.methodologies.evolver.methodology_evolver_memory_summary",
        "MethodologyEvolverMemorySummary",
    ),
    "MethodologyEvolverMemorySupport": (
        "research_system.services.methodologies.evolver.methodology_evolver_memory_support",
        "MethodologyEvolverMemorySupport",
    ),
    "MethodologyEvolverProtocolSupport": (
        "research_system.services.methodologies.evolver.methodology_evolver_protocol_support",
        "MethodologyEvolverProtocolSupport",
    ),
    "MethodologyEvolverReviewAggregation": (
        "research_system.services.methodologies.evolver.methodology_evolver_review_aggregation",
        "MethodologyEvolverReviewAggregation",
    ),
    "MethodologyEvolverReviewCandidates": (
        "research_system.services.methodologies.evolver.methodology_evolver_review_candidates",
        "MethodologyEvolverReviewCandidates",
    ),
    "MethodologyEvolverValuationAggregation": (
        "research_system.services.methodologies.evolver.methodology_evolver_valuation_aggregation",
        "MethodologyEvolverValuationAggregation",
    ),
    "MethodologyEvolverValuationCandidates": (
        "research_system.services.methodologies.evolver.methodology_evolver_valuation_candidates",
        "MethodologyEvolverValuationCandidates",
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
