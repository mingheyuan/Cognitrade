from __future__ import annotations

from importlib import import_module

__all__ = [
    "CashflowResilienceService",
    "CompsService",
    "DCFService",
    "DimensionNormalizer",
    "DimensionNormalizerIndex",
    "DimensionTaxonomyEntry",
    "MethodologyEvolver",
    "MethodologyEvolverAggregation",
    "MethodologyEvolverExtraction",
    "MethodologyEvolverMemorySupport",
    "MethodologyManifestService",
    "PlanningContextView",
    "RetrievalPolicyService",
    "build_planning_context_view",
    "derive_methodology_focus",
    "normalize_planning_context_hints",
    "normalize_retrieval_policy",
    "resolve_review_focus",
]

_EXPORTS = {
    "CashflowResilienceService": (
        "research_system.services.methodologies.valuation.cashflow_resilience_service",
        "CashflowResilienceService",
    ),
    "CompsService": ("research_system.services.methodologies.valuation.comps_service", "CompsService"),
    "DCFService": ("research_system.services.methodologies.valuation.dcf_service", "DCFService"),
    "DimensionNormalizer": ("research_system.services.methodologies.dimensions.dimension_normalizer", "DimensionNormalizer"),
    "DimensionNormalizerIndex": (
        "research_system.services.methodologies.dimensions.dimension_normalizer_query_support",
        "DimensionNormalizerIndex",
    ),
    "DimensionTaxonomyEntry": (
        "research_system.services.methodologies.dimensions.dimension_normalizer_defaults",
        "DimensionTaxonomyEntry",
    ),
    "MethodologyEvolver": ("research_system.services.methodologies.evolver.methodology_evolver", "MethodologyEvolver"),
    "MethodologyEvolverAggregation": (
        "research_system.services.methodologies.evolver.methodology_evolver_aggregation",
        "MethodologyEvolverAggregation",
    ),
    "MethodologyEvolverExtraction": (
        "research_system.services.methodologies.evolver.methodology_evolver_extraction",
        "MethodologyEvolverExtraction",
    ),
    "MethodologyEvolverMemorySupport": (
        "research_system.services.methodologies.evolver.methodology_evolver_memory_support",
        "MethodologyEvolverMemorySupport",
    ),
    "MethodologyManifestService": (
        "research_system.services.methodologies.policy.methodology_manifest_service",
        "MethodologyManifestService",
    ),
    "PlanningContextView": ("research_system.services.methodologies.planning.planning_context_view", "PlanningContextView"),
    "RetrievalPolicyService": (
        "research_system.services.methodologies.policy.retrieval_policy_service",
        "RetrievalPolicyService",
    ),
    "build_planning_context_view": (
        "research_system.services.methodologies.planning.planning_context_view",
        "build_planning_context_view",
    ),
    "derive_methodology_focus": (
        "research_system.services.methodologies.planning.planning_context_methodology",
        "derive_methodology_focus",
    ),
    "normalize_planning_context_hints": (
        "research_system.services.methodologies.planning.planning_context_methodology",
        "normalize_planning_context_hints",
    ),
    "normalize_retrieval_policy": (
        "research_system.services.methodologies.policy.retrieval_policy_view",
        "normalize_retrieval_policy",
    ),
    "resolve_review_focus": (
        "research_system.services.methodologies.policy.review_focus_support",
        "resolve_review_focus",
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
