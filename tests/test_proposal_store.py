from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.proposal_store import ProposalStore
from research_system.schemas.proposals import ProposalDraft, ProposalReviewEvent


class ProposalStoreTests(unittest.TestCase):
    def test_find_and_list_all_cover_cross_status_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProposalStore(Path(temp_dir))
            pending = ProposalDraft(
                proposal_type="MethodologyTemplatePatch",
                status="pending",
                title="Pending dimension",
                summary="pending",
                author_agent="tester",
                sample_size=1,
                benchmark_scope="dimension",
                evidence_summary="{}",
            )
            approved = ProposalDraft(
                proposal_type="MemoryLearningUpdate",
                status="approved",
                title="Approved pattern",
                summary="approved",
                author_agent="tester",
                sample_size=3,
                benchmark_scope="review_pattern",
                evidence_summary="{}",
            )
            store.create(pending)
            store.create(approved)

            rows = store.list_all()
            self.assertEqual({row.proposal_id for row in rows}, {pending.proposal_id, approved.proposal_id})

            located = store.find(approved.proposal_id)
            self.assertIsNotNone(located)
            proposal, status = located or (None, None)
            self.assertEqual(status, "approved")
            self.assertEqual(proposal.proposal_id, approved.proposal_id)

    def test_save_persists_review_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProposalStore(Path(temp_dir))
            proposal = ProposalDraft(
                proposal_type="MethodologyTemplatePatch",
                status="pending",
                title="Pending dimension",
                summary="pending",
                author_agent="tester",
                sample_size=1,
                benchmark_scope="dimension",
                evidence_summary="{}",
            )
            store.create(proposal)
            proposal.review_history.append(
                ProposalReviewEvent(
                    decision="pending",
                    reviewer="system_threshold_gate",
                    reason="pending_manual_review_dimension_sample_size<2",
                    source="auto_gate",
                )
            )
            store.save(proposal, previous_status="pending")

            reloaded = store.load("pending", proposal.proposal_id)

            assert reloaded is not None
            self.assertEqual(len(reloaded.review_history), 1)
            self.assertEqual(reloaded.review_history[0].reviewer, "system_threshold_gate")


if __name__ == "__main__":
    unittest.main()
