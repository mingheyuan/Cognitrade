from __future__ import annotations

from importlib import import_module

__all__ = [
    "PlanningContextView",
    "build_planning_context_view",
    "combine_focus_text",
    "derive_methodology_focus",
    "has_cashflow_focus",
    "has_long_term_bias",
    "has_mid_term_bias",
    "has_momentum_focus",
    "has_notice_focus",
    "has_policy_focus",
    "has_same_basis_focus",
    "has_valuation_focus",
    "methodology_from_benchmark_scope",
    "methodology_preference_from_hints",
    "methodology_preference_order",
    "methodology_preference_triplet",
    "normalize_methodology_preference",
    "normalize_methodology_preferences",
    "normalize_planning_context_hints",
]

_EXPORTS = {
    "PlanningContextView": ("research_system.services.methodologies.planning.planning_context_view", "PlanningContextView"),
    "build_planning_context_view": (
        "research_system.services.methodologies.planning.planning_context_view",
        "build_planning_context_view",
    ),
    "combine_focus_text": ("research_system.services.methodologies.planning.planning_context_methodology", "combine_focus_text"),
    "derive_methodology_focus": (
        "research_system.services.methodologies.planning.planning_context_methodology",
        "derive_methodology_focus",
    ),
    "has_cashflow_focus": ("research_system.services.methodologies.planning.planning_context_signals", "has_cashflow_focus"),
    "has_long_term_bias": ("research_system.services.methodologies.planning.planning_context_signals", "has_long_term_bias"),
    "has_mid_term_bias": ("research_system.services.methodologies.planning.planning_context_signals", "has_mid_term_bias"),
    "has_momentum_focus": ("research_system.services.methodologies.planning.planning_context_signals", "has_momentum_focus"),
    "has_notice_focus": ("research_system.services.methodologies.planning.planning_context_signals", "has_notice_focus"),
    "has_policy_focus": ("research_system.services.methodologies.planning.planning_context_signals", "has_policy_focus"),
    "has_same_basis_focus": ("research_system.services.methodologies.planning.planning_context_signals", "has_same_basis_focus"),
    "has_valuation_focus": ("research_system.services.methodologies.planning.planning_context_signals", "has_valuation_focus"),
    "methodology_from_benchmark_scope": (
        "research_system.services.methodologies.planning.planning_context_methodology",
        "methodology_from_benchmark_scope",
    ),
    "methodology_preference_from_hints": (
        "research_system.services.methodologies.planning.planning_context_methodology",
        "methodology_preference_from_hints",
    ),
    "methodology_preference_order": (
        "research_system.services.methodologies.planning.planning_context_view",
        "methodology_preference_order",
    ),
    "methodology_preference_triplet": (
        "research_system.services.methodologies.planning.planning_context_methodology",
        "methodology_preference_triplet",
    ),
    "normalize_methodology_preference": (
        "research_system.services.methodologies.planning.planning_context_methodology",
        "normalize_methodology_preference",
    ),
    "normalize_methodology_preferences": (
        "research_system.services.methodologies.planning.planning_context_methodology",
        "normalize_methodology_preferences",
    ),
    "normalize_planning_context_hints": (
        "research_system.services.methodologies.planning.planning_context_methodology",
        "normalize_planning_context_hints",
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
