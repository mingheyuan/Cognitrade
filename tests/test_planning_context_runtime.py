from __future__ import annotations

import unittest

from research_system.services.planning_context import (
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


class PlanningContextRuntimeTests(unittest.TestCase):
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
