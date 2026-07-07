from __future__ import annotations

import unittest

from research_system.services.proposals.generator import ProposalGenerator


class ProposalGeneratorTests(unittest.TestCase):
    def test_build_generates_memory_learning_update_for_review_pattern(self) -> None:
        generator = ProposalGenerator()

        proposals = generator.build(
            author_agent="reviewer",
            candidates=[
                {
                    "candidate_type": "review_pattern",
                    "title": "review_pattern_summary",
                    "sample_size": 3,
                    "related_run_ids": ["01A", "01B", "01C"],
                    "severity_counts": {"high": 2, "medium": 1},
                    "verdict_counts": {"rejected": 2, "confirmed": 1},
                }
            ],
        )

        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]
        self.assertEqual(proposal.proposal_type, "MemoryLearningUpdate")
        self.assertEqual(proposal.sample_size, 3)
        self.assertEqual(proposal.benchmark_scope, "review_pattern")
        self.assertIn("severity_counts", proposal.evidence_summary)
        self.assertIn("verdict_counts", proposal.evidence_summary)

    def test_build_dimension_and_valuation_proposals_include_confidence_metrics(self) -> None:
        generator = ProposalGenerator()

        proposals = generator.build(
            author_agent="methodology_evolver",
            candidates=[
                {
                    "candidate_type": "dimension",
                    "title": "policy catalyst",
                    "run_id": "01DIM",
                    "sample_size": 2,
                    "dimension_family": "event_driven",
                    "scenario_tags": ["short_term"],
                    "methodology_tags": ["event_driven"],
                    "confidence": 0.72,
                    "memory_match_score": 4.0,
                    "memory_support": {"context_count": 2, "entity_scope_count": 2},
                    "aggregated_evidence_summary": {"supported_claim_count": 2},
                },
                {
                    "candidate_type": "valuation_heuristic",
                    "title": "dcf",
                    "run_id": "01VAL",
                    "sample_size": 2,
                    "methodology_type": "dcf",
                    "assumption_tags": ["light_dcf"],
                    "sensitivity_tags": ["wacc"],
                    "confidence": 0.61,
                    "memory_match_score": 3.0,
                    "memory_support": {"context_count": 1, "entity_scope_count": 1},
                    "aggregated_evidence_summary": {"warnings_count": 0},
                },
            ],
        )

        dimension = next(item for item in proposals if item.proposal_type == "MethodologyTemplatePatch")
        valuation = next(item for item in proposals if item.proposal_type == "ValuationHeuristicPatch")
        self.assertEqual(dimension.before_metrics["average_confidence"], 0.72)
        self.assertEqual(dimension.before_metrics["memory_match_score"], 4.0)
        self.assertEqual(dimension.before_metrics["memory_support"]["context_count"], 2)
        self.assertEqual(valuation.before_metrics["average_confidence"], 0.61)
        self.assertEqual(valuation.before_metrics["memory_match_score"], 3.0)
        self.assertEqual(valuation.before_metrics["memory_support"]["entity_scope_count"], 1)


if __name__ == "__main__":
    unittest.main()
