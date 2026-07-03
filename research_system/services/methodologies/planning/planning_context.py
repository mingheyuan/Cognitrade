from __future__ import annotations

from research_system.services.methodologies.planning.planning_context_methodology import (
    combine_focus_text,
    derive_methodology_focus,
    methodology_from_benchmark_scope,
    methodology_preference_from_hints,
    methodology_preference_triplet,
    normalize_methodology_preference,
    normalize_methodology_preferences,
    normalize_planning_context_hints,
)
from research_system.services.methodologies.planning.planning_context_signals import (
    has_cashflow_focus,
    has_long_term_bias,
    has_mid_term_bias,
    has_momentum_focus,
    has_notice_focus,
    has_policy_focus,
    has_same_basis_focus,
    has_valuation_focus,
)
from research_system.services.methodologies.planning.planning_context_view import (
    PlanningContextView,
    build_planning_context_view,
    methodology_preference_order,
)
