from __future__ import annotations

import unittest

from research_system.schemas.proposals import ProposalDraft, ProposalReviewEvent
from research_system.services.proposal_payloads import (
    proposal_detail_fields,
    proposal_detail_payload,
    proposal_display_summary,
    proposal_result_sections,
    render_proposal_review_history_line,
    render_proposal_row_text,
    proposal_summary_row,
)


class ProposalPayloadsTests(unittest.TestCase):
    def test_proposal_display_summary_uses_stable_shape(self) -> None:
        summary = proposal_display_summary(
            {
                "proposal_id": "01PROP",
                "status": "pending",
                "proposal_type": "MethodologyTemplatePatch",
                "title": "Promote policy catalyst",
            }
        )
        self.assertEqual(summary, "01PROP [pending] MethodologyTemplatePatch Promote policy catalyst")

    def test_proposal_detail_payload_includes_review_history(self) -> None:
        proposal = ProposalDraft(
            proposal_id="01PROP",
            proposal_type="MethodologyTemplatePatch",
            status="approved",
            title="Promote policy catalyst",
            summary="Promote candidate dimension",
            author_agent="tester",
            sample_size=2,
            benchmark_scope="event_driven",
            evidence_summary="{}",
            review_history=[
                ProposalReviewEvent(
                    decision="pending",
                    reviewer="system_threshold_gate",
                    reason="pending_manual_review_dimension_sample_size<2",
                    source="auto_gate",
                )
            ],
        )

        payload = proposal_detail_payload(proposal, stored_status="approved")

        self.assertEqual(payload["stored_status"], "approved")
        self.assertEqual(len(payload["review_history"]), 1)
        self.assertEqual(payload["review_history"][0]["reviewer"], "system_threshold_gate")
        self.assertIn("display_summary", payload)

    def test_proposal_summary_row_uses_shared_display_summary(self) -> None:
        proposal = ProposalDraft(
            proposal_id="01PROP",
            proposal_type="MethodologyTemplatePatch",
            status="pending",
            title="Promote policy catalyst",
            summary="Promote candidate dimension",
            author_agent="tester",
            sample_size=1,
            benchmark_scope="event_driven",
            evidence_summary="{}",
        )

        row = proposal_summary_row(proposal)

        self.assertEqual(row["proposal_id"], "01PROP")
        self.assertEqual(row["display_summary"], "01PROP [pending] MethodologyTemplatePatch Promote policy catalyst")

    def test_proposal_detail_fields_use_shared_order(self) -> None:
        self.assertEqual(
            proposal_detail_fields(
                {
                    "proposal_id": "01PROP",
                    "status": "pending",
                    "proposal_type": "MethodologyTemplatePatch",
                    "title": "Promote policy catalyst",
                    "summary": "Promote candidate dimension",
                    "sample_size": 1,
                    "benchmark_scope": "event_driven",
                    "review_reason": "pending_manual_review_dimension_sample_size<2",
                    "reviewer": "system_threshold_gate",
                    "reviewed_at": "2026-07-01T00:00:00+00:00",
                    "created_from_run_id": "01RUN",
                }
            ),
            [
                ("proposal_id", "01PROP"),
                ("status", "pending"),
                ("proposal_type", "MethodologyTemplatePatch"),
                ("title", "Promote policy catalyst"),
                ("summary", "Promote candidate dimension"),
                ("sample_size", "1"),
                ("benchmark_scope", "event_driven"),
                ("review_reason", "pending_manual_review_dimension_sample_size<2"),
                ("reviewer", "system_threshold_gate"),
                ("reviewed_at", "2026-07-01T00:00:00+00:00"),
                ("created_from_run_id", "01RUN"),
            ],
        )

    def test_proposal_result_sections_preserve_known_section_order(self) -> None:
        self.assertEqual(
            proposal_result_sections(
                {
                    "result": {
                        "retrieval_policy": {"approved_proposal_ids": ["01PROP"]},
                        "manifest": {"version": 1},
                        "memory_record": {"memory_id": "mem_01"},
                    }
                }
            ),
            [
                {"manifest": {"version": 1}},
                {"retrieval_policy": {"approved_proposal_ids": ["01PROP"]}},
                {"memory_record": {"memory_id": "mem_01"}},
            ],
        )

    def test_render_proposal_row_text_matches_cli_shape(self) -> None:
        text = render_proposal_row_text(
            {
                "proposal_id": "01PROP",
                "status": "pending",
                "proposal_type": "MethodologyTemplatePatch",
                "title": "Promote policy catalyst",
                "sample_size": 1,
                "review_reason": "pending_manual_review_dimension_sample_size<2",
            }
        )
        self.assertEqual(
            text,
            "01PROP  [pending]  MethodologyTemplatePatch  Promote policy catalyst  sample_size=1  reason=pending_manual_review_dimension_sample_size<2",
        )

    def test_render_proposal_review_history_line_matches_cli_shape(self) -> None:
        text = render_proposal_review_history_line(
            {
                "decision": "pending",
                "reviewer": "system_threshold_gate",
                "source": "auto_gate",
                "previous_status": "pending",
                "reason": "pending_manual_review_dimension_sample_size<2",
            }
        )
        self.assertEqual(
            text,
            "  [pending] reviewer=system_threshold_gate source=auto_gate from=pending reason=pending_manual_review_dimension_sample_size<2",
        )


if __name__ == "__main__":
    unittest.main()
