from __future__ import annotations

import unittest

from research_system.services.methodologies.planning import (
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


class PlanningContextTests(unittest.TestCase):
    def test_normalize_planning_context_hints_filters_blank_entries(self) -> None:
        payload = normalize_planning_context_hints(
            {
                "recommended_next_step": " validate cashflow resilience ",
                "open_question": "",
                "thread_topic": " same-basis compare ",
                "ignored": None,
            }
        )

        self.assertEqual(
            payload,
            {
                "recommended_next_step": "validate cashflow resilience",
                "thread_topic": "same-basis compare",
            },
        )

    def test_build_planning_context_view_combines_query_hints_and_extra_text(self) -> None:
        context = build_planning_context_view(
            query="Compare these stocks",
            planning_context_hints={
                "recommended_next_step": "keep same-basis assumptions explicit",
                "thread_topic": "peer compare",
            },
            extra_texts=["valuation summary"],
        )

        self.assertIn("compare these stocks", context.combined_text)
        self.assertIn("keep same-basis assumptions explicit", context.combined_text)
        self.assertIn("valuation summary", context.combined_text)
        self.assertEqual(context.thread_topic, "peer compare")

    def test_focus_helpers_detect_expected_tokens(self) -> None:
        self.assertTrue(has_cashflow_focus("validate cashflow resilience and debt tolerance"))
        self.assertTrue(has_same_basis_focus("same-basis peer compare"))
        self.assertTrue(has_valuation_focus("valuation and dcf refresh"))
        self.assertTrue(has_policy_focus("policy catalyst and regulatory update"))
        self.assertTrue(has_momentum_focus("short-term sentiment and momentum"))
        self.assertTrue(has_long_term_bias("long-term compounder"))
        self.assertTrue(has_mid_term_bias("mid-term setup"))
        self.assertTrue(has_notice_focus("notice and filing review"))

    def test_methodology_hint_helpers_map_benchmark_scope(self) -> None:
        self.assertEqual(methodology_from_benchmark_scope("defensive_compounder"), "cashflow_resilience")
        self.assertEqual(methodology_from_benchmark_scope("event_driven"), "comps")
        self.assertEqual(methodology_from_benchmark_scope("intrinsic_valuation"), "dcf")
        self.assertEqual(methodology_preference_from_hints({"methodology_benchmark_scope": "event_driven"}), "comps")
        self.assertEqual(methodology_preference_triplet("dcf"), ["dcf", "comps", "cashflow_resilience"])
        self.assertEqual(normalize_methodology_preference("event_driven"), "comps")
        self.assertEqual(
            normalize_methodology_preferences(["event_driven", "dcf", "event_driven", "cashflow_resilience"]),
            ["comps", "dcf", "cashflow_resilience"],
        )

    def test_derive_methodology_focus_prefers_cashflow_text_then_methodology_memory_hint(self) -> None:
        focus_text = combine_focus_text("continue previous work", "validate cashflow resilience")
        self.assertEqual(
            derive_methodology_focus(
                cashflow_text=focus_text,
                comparison_text=focus_text,
                planning_context_hints={"methodology_benchmark_scope": "event_driven"},
            ),
            ("cashflow_resilience", "planning_context"),
        )
        self.assertEqual(
            derive_methodology_focus(
                cashflow_text="continue previous work",
                comparison_text="continue previous work",
                planning_context_hints={"methodology_benchmark_scope": "event_driven"},
            ),
            ("comps", "methodology_memory"),
        )

    def test_methodology_preference_order_prioritizes_cashflow_from_query(self) -> None:
        order = methodology_preference_order(
            query="Which one has better cashflow resilience?",
            planning_context_hints={},
        )

        self.assertEqual(order, ["cashflow_resilience", "dcf", "comps"])

    def test_methodology_preference_order_prioritizes_same_basis_from_thread_hints(self) -> None:
        order = methodology_preference_order(
            query="Who is better?",
            planning_context_hints={
                "recommended_next_step": "keep same-basis comparison explicit",
                "thread_topic": "peer compare",
            },
        )

        self.assertEqual(order, ["comps", "dcf", "cashflow_resilience"])

    def test_methodology_preference_order_falls_back_to_valuation_hint(self) -> None:
        order = methodology_preference_order(
            query="What next?",
            planning_context_hints={
                "recommended_next_step": "refresh valuation assumptions",
            },
        )

        self.assertEqual(order, ["dcf", "comps", "cashflow_resilience"])

    def test_methodology_preference_order_falls_back_to_methodology_benchmark_scope_hint(self) -> None:
        order = methodology_preference_order(
            query="continue the previous work",
            planning_context_hints={
                "methodology_benchmark_scope": "defensive_compounder",
            },
        )

        self.assertEqual(order, ["cashflow_resilience", "dcf", "comps"])


if __name__ == "__main__":
    unittest.main()
