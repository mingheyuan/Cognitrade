from __future__ import annotations

import unittest

from research_system.services.methodologies.planning import (
    PlanningContextView,
    build_planning_context_view,
    combine_focus_text,
    derive_methodology_focus,
    has_cashflow_focus,
    has_long_term_bias,
    has_mid_term_bias,
    has_momentum_focus,
    has_notice_focus,
    has_policy_focus,
    has_same_basis_focus,
    has_valuation_focus,
    methodology_from_benchmark_scope,
    methodology_preference_from_hints,
    methodology_preference_order,
    methodology_preference_triplet,
    normalize_methodology_preference,
    normalize_methodology_preferences,
    normalize_planning_context_hints,
)
from research_system.services.methodologies.planning import (
    PlanningContextView as PackagePlanningContextView,
)
from research_system.services.methodologies.planning import (
    build_planning_context_view as package_build_planning_context_view,
)
from research_system.services.methodologies.planning import combine_focus_text as package_combine_focus_text
from research_system.services.methodologies.planning import (
    derive_methodology_focus as package_derive_methodology_focus,
)
from research_system.services.methodologies.planning import has_cashflow_focus as package_has_cashflow_focus
from research_system.services.methodologies.planning import has_long_term_bias as package_has_long_term_bias
from research_system.services.methodologies.planning import has_mid_term_bias as package_has_mid_term_bias
from research_system.services.methodologies.planning import has_momentum_focus as package_has_momentum_focus
from research_system.services.methodologies.planning import has_notice_focus as package_has_notice_focus
from research_system.services.methodologies.planning import has_policy_focus as package_has_policy_focus
from research_system.services.methodologies.planning import has_same_basis_focus as package_has_same_basis_focus
from research_system.services.methodologies.planning import has_valuation_focus as package_has_valuation_focus
from research_system.services.methodologies.planning import (
    methodology_from_benchmark_scope as package_methodology_from_benchmark_scope,
)
from research_system.services.methodologies.planning import (
    methodology_preference_from_hints as package_methodology_preference_from_hints,
)
from research_system.services.methodologies.planning import (
    methodology_preference_order as package_methodology_preference_order,
)
from research_system.services.methodologies.planning import (
    methodology_preference_triplet as package_methodology_preference_triplet,
)
from research_system.services.methodologies.planning import (
    normalize_methodology_preference as package_normalize_methodology_preference,
)
from research_system.services.methodologies.planning import (
    normalize_methodology_preferences as package_normalize_methodology_preferences,
)
from research_system.services.methodologies.planning import (
    normalize_planning_context_hints as package_normalize_planning_context_hints,
)


class PlanningContextRuntimeTests(unittest.TestCase):
    def test_planning_package_exports_match_top_level_compat_exports(self) -> None:
        self.assertIs(PlanningContextView, PackagePlanningContextView)
        self.assertIs(build_planning_context_view, package_build_planning_context_view)
        self.assertIs(combine_focus_text, package_combine_focus_text)
        self.assertIs(derive_methodology_focus, package_derive_methodology_focus)
        self.assertIs(has_cashflow_focus, package_has_cashflow_focus)
        self.assertIs(has_same_basis_focus, package_has_same_basis_focus)
        self.assertIs(has_valuation_focus, package_has_valuation_focus)
        self.assertIs(has_policy_focus, package_has_policy_focus)
        self.assertIs(has_momentum_focus, package_has_momentum_focus)
        self.assertIs(has_long_term_bias, package_has_long_term_bias)
        self.assertIs(has_mid_term_bias, package_has_mid_term_bias)
        self.assertIs(has_notice_focus, package_has_notice_focus)
        self.assertIs(methodology_from_benchmark_scope, package_methodology_from_benchmark_scope)
        self.assertIs(methodology_preference_from_hints, package_methodology_preference_from_hints)
        self.assertIs(methodology_preference_triplet, package_methodology_preference_triplet)
        self.assertIs(normalize_methodology_preference, package_normalize_methodology_preference)
        self.assertIs(normalize_methodology_preferences, package_normalize_methodology_preferences)
        self.assertIs(normalize_planning_context_hints, package_normalize_planning_context_hints)
        self.assertIs(methodology_preference_order, package_methodology_preference_order)

    def test_planning_context_facade_exports_expected_helpers(self) -> None:
        self.assertTrue(callable(normalize_planning_context_hints))
        self.assertTrue(callable(methodology_from_benchmark_scope))
        self.assertTrue(callable(methodology_preference_from_hints))
        self.assertTrue(callable(methodology_preference_triplet))
        self.assertTrue(callable(normalize_methodology_preference))
        self.assertTrue(callable(normalize_methodology_preferences))
        self.assertTrue(callable(combine_focus_text))
        self.assertTrue(callable(derive_methodology_focus))
        self.assertTrue(callable(build_planning_context_view))
        self.assertTrue(callable(methodology_preference_order))
        self.assertTrue(callable(has_cashflow_focus))
        self.assertTrue(callable(has_same_basis_focus))
        self.assertTrue(callable(has_valuation_focus))
        self.assertTrue(callable(has_policy_focus))
        self.assertTrue(callable(has_momentum_focus))
        self.assertTrue(callable(has_long_term_bias))
        self.assertTrue(callable(has_mid_term_bias))
        self.assertTrue(callable(has_notice_focus))
        self.assertIsInstance(build_planning_context_view(query="x"), PlanningContextView)


if __name__ == "__main__":
    unittest.main()
