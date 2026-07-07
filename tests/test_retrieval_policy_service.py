from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.proposals import ProposalDraft
from research_system.services.methodologies.policy.retrieval_policy_service import (
    RetrievalPolicyService,
)


class RetrievalPolicyServiceTests(unittest.TestCase):
    def test_load_normalizes_defaults_and_preferred_methodologies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.retrieval_policy_path.parent.mkdir(parents=True, exist_ok=True)
            settings.retrieval_policy_path.write_text(
                "preferred_methodologies:\n"
                "  - event_driven\n"
                "  - dcf\n"
                "final_top_k: '0'\n"
                "recency_window_days: bad\n"
                "rerank_enabled: 1\n",
                encoding="utf-8",
            )
            service = RetrievalPolicyService(settings)

            payload = service.load()

            self.assertEqual(payload["preferred_methodologies"], ["comps", "dcf"])
            self.assertEqual(payload["final_top_k"], 5)
            self.assertEqual(payload["recency_window_days"], 30)
            self.assertTrue(payload["rerank_enabled"])

    def test_approve_updates_policy_for_valuation_and_dimension_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            service = RetrievalPolicyService(settings)

            dimension = ProposalDraft(
                proposal_type="MethodologyTemplatePatch",
                status="approved",
                title="Promote policy catalyst",
                summary="Promote policy catalyst dimension",
                author_agent="tester",
                sample_size=2,
                benchmark_scope="event_driven",
                evidence_summary="{}",
            )
            valuation = ProposalDraft(
                proposal_type="ValuationHeuristicPatch",
                status="approved",
                title="Capture dcf heuristic",
                summary="Capture valuation heuristic",
                author_agent="tester",
                sample_size=2,
                benchmark_scope="dcf",
                evidence_summary="{}",
            )

            first = service.approve(dimension)
            second = service.approve(valuation)

            self.assertIn(dimension.proposal_id, first["approved_proposal_ids"])
            self.assertGreaterEqual(float(first["score_weights"]["metadata_match"]), 1.1)
            self.assertIn(valuation.proposal_id, second["approved_proposal_ids"])
            self.assertIn("dcf", second["preferred_methodologies"])


if __name__ == "__main__":
    unittest.main()
