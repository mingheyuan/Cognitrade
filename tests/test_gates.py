from __future__ import annotations

import unittest

from research_system.runtime.gates import GateEvaluator
from research_system.schemas.runtime import BlackboardEntry


class GateEvaluatorTests(unittest.TestCase):
    def test_review_gate_passes_when_review_finding_exists(self) -> None:
        gate = GateEvaluator()
        entries = [
            BlackboardEntry(
                run_id="01GATEPASS",
                request_id="req-pass",
                kind="source_record",
                agent="reviewer",
                phase="review",
                payload={
                    "title": "review_summary",
                    "content": "{'review_required': True, 'finding_count': 1}",
                    "source_type": "review_summary",
                    "summary": {
                        "review_required": True,
                        "finding_count": 1,
                        "notes": [],
                    },
                },
                source_refs=[{"type": "posterior_review", "run_id": "01GATEPASS"}],
            ),
            BlackboardEntry(
                run_id="01GATEPASS",
                request_id="req-pass",
                kind="review_finding",
                agent="reviewer",
                phase="review",
                payload={
                    "claim_id": "claim-1",
                    "verdict": "confirmed",
                    "notes": "benchmark-relative underperformance confirmed",
                },
                source_refs=[{"type": "posterior_review", "run_id": "01GATEPASS"}],
            ),
            BlackboardEntry(
                run_id="01GATEPASS",
                request_id="req-pass",
                kind="report_fragment",
                agent="reporter",
                phase="report",
                payload={
                    "conclusion": "confirmed=1 rejected=0 inconclusive=0",
                    "risk": "posterior review degraded",
                },
                source_refs=[{"type": "retrieval", "run_id": "01GATEPASS"}],
            ),
        ]

        verdicts = gate.evaluate(
            run_id="01GATEPASS",
            phase="report",
            entries=entries,
            requires_verified_snapshot=False,
            requires_review_findings=True,
        )

        review_gate = next(verdict for verdict in verdicts if verdict.gate_type == "ReviewGate")
        self.assertEqual(review_gate.verdict, "pass")

    def test_review_gate_marks_needs_review_when_only_summary_exists(self) -> None:
        gate = GateEvaluator()
        entries = [
            BlackboardEntry(
                run_id="01GATEREVIEW",
                request_id="req-review",
                kind="source_record",
                agent="reviewer",
                phase="review",
                payload={
                    "title": "review_summary",
                    "content": "{'review_required': True, 'finding_count': 0, 'notes': ['missing_sector_snapshot']}",
                    "source_type": "review_summary",
                    "summary": {
                        "review_required": True,
                        "finding_count": 0,
                        "notes": ["missing_sector_snapshot"],
                    },
                },
                source_refs=[{"type": "posterior_review", "run_id": "01GATEREVIEW"}],
            ),
            BlackboardEntry(
                run_id="01GATEREVIEW",
                request_id="req-review",
                kind="report_fragment",
                agent="reporter",
                phase="report",
                payload={
                    "conclusion": "confirmed=0 rejected=0 inconclusive=0",
                    "risk": "posterior review degraded",
                },
                source_refs=[{"type": "retrieval", "run_id": "01GATEREVIEW"}],
            ),
        ]

        verdicts = gate.evaluate(
            run_id="01GATEREVIEW",
            phase="report",
            entries=entries,
            requires_verified_snapshot=False,
            requires_review_findings=True,
        )

        review_gate = next(verdict for verdict in verdicts if verdict.gate_type == "ReviewGate")
        self.assertEqual(review_gate.verdict, "needs_review")
        self.assertIn("missing_sector_snapshot", review_gate.blocking_items)

    def test_review_gate_is_not_added_for_normal_phase_runs(self) -> None:
        gate = GateEvaluator()
        entries = [
            BlackboardEntry(
                run_id="01GATENORMAL",
                request_id="req-normal",
                kind="source_record",
                agent="collector",
                phase="collect",
                payload={
                    "title": "stock_check input query",
                    "content": "看看000001",
                    "source_type": "user_query",
                },
                source_refs=[{"type": "request", "run_id": "01GATENORMAL"}],
            ),
            BlackboardEntry(
                run_id="01GATENORMAL",
                request_id="req-normal",
                kind="report_fragment",
                agent="reporter",
                phase="report",
                payload={
                    "conclusion": "sample report",
                    "risk": "sample risk",
                },
                source_refs=[{"type": "retrieval", "run_id": "01GATENORMAL"}],
            ),
        ]

        verdicts = gate.evaluate(
            run_id="01GATENORMAL",
            phase="report",
            entries=entries,
            requires_verified_snapshot=False,
        )

        gate_types = {verdict.gate_type for verdict in verdicts}
        self.assertNotIn("ReviewGate", gate_types)


if __name__ == "__main__":
    unittest.main()
