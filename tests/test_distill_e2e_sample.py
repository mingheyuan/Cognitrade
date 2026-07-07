from __future__ import annotations

import unittest
from pathlib import Path

from research_system.agents.critic.service import CriticService
from research_system.agents.distiller.service import DistillerService
from research_system.agents.reporter.service import ReporterService
from research_system.agents.retriever.service import RetrieverService
from research_system.schemas.runtime import BlackboardEntry


class UnavailableProviderStub:
    available = False


class DistillE2ESampleTests(unittest.TestCase):
    def _build_blackboard_entries(
        self,
        *,
        distill_result: dict,
        critic_result: dict,
    ) -> list[dict]:
        run_id = "01TESTDISTILLE2E"
        request_id = "01TESTREQUESTE2E"
        phase = "phase_2"

        entries: list[dict] = []
        for block in distill_result["logic_blocks"]:
            entries.append(
                BlackboardEntry(
                    run_id=run_id,
                    thread_id=run_id,
                    request_id=request_id,
                    kind="logic_block",
                    agent="distiller",
                    phase=phase,
                    payload=block,
                    source_refs=[
                        {
                            "type": "distillation",
                            "source_record_id": block.get("source_record_id"),
                            "run_id": run_id,
                        }
                    ],
                    confidence=block.get("confidence"),
                    risk_flags=block.get("risk_flags") or [],
                ).model_dump(mode="json")
            )

        for dimension in distill_result["distilled_dimensions"]:
            entries.append(
                BlackboardEntry(
                    run_id=run_id,
                    thread_id=run_id,
                    request_id=request_id,
                    kind="distilled_dimension",
                    agent="distiller",
                    phase=phase,
                    payload=dimension,
                    source_refs=[
                        {
                            "type": "distillation",
                            "supported_claim_block_ids": dimension.get("supported_claim_block_ids", []),
                            "run_id": run_id,
                        }
                    ],
                    confidence=dimension.get("confidence"),
                ).model_dump(mode="json")
            )

        entries.append(
            BlackboardEntry(
                run_id=run_id,
                thread_id=run_id,
                request_id=request_id,
                kind="source_record",
                agent="distiller",
                phase=phase,
                payload={
                    "title": "distillation_summary",
                    "content": str(distill_result["summary"]),
                    "source_type": "distillation_summary",
                    "summary": distill_result["summary"],
                },
                source_refs=[{"type": "distillation", "run_id": run_id}],
                confidence=distill_result["summary"].get("overall_confidence"),
                risk_flags=distill_result["summary"].get("major_risks") or [],
            ).model_dump(mode="json")
        )

        entries.append(
            BlackboardEntry(
                run_id=run_id,
                thread_id=run_id,
                request_id=request_id,
                kind="source_record",
                agent="critic",
                phase=phase,
                payload={
                    "title": "distillation_scorecard",
                    "content": str(critic_result["scorecard"]),
                    "source_type": "distillation_scorecard",
                    "scorecard": critic_result["scorecard"],
                },
                source_refs=[{"type": "critic_review", "run_id": run_id}],
                confidence=1.0,
            ).model_dump(mode="json")
        )

        for finding in critic_result["findings"]:
            entries.append(
                BlackboardEntry(
                    run_id=run_id,
                    thread_id=run_id,
                    request_id=request_id,
                    kind="review_finding",
                    agent="critic",
                    phase=phase,
                    status="needs_review" if finding.get("verdict") == "needs_review" else "active",
                    payload=finding,
                    source_refs=[{"type": "critic_review", "run_id": run_id}],
                    confidence=1.0,
                    risk_flags=[finding.get("finding_type")] if finding.get("finding_type") else [],
                ).model_dump(mode="json")
            )

        return entries

    def test_sample_statuses_flow_produces_scorecard_and_report(self) -> None:
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "xueqiu_statuses_sample.jsonl"

        distiller = DistillerService(UnavailableProviderStub())
        critic = CriticService()
        retriever = object.__new__(RetrieverService)
        reporter = ReporterService(UnavailableProviderStub())

        distill_result = distiller.run(
            query=f"distill kol from {fixture_path}",
            source_records=[
                {
                    "title": "sample distill request",
                    "content": str(fixture_path),
                    "source_type": "xueqiu_statuses_jsonl",
                    "path": str(fixture_path),
                }
            ],
        )

        critic_result = critic.run(
            distillation_summary=distill_result["summary"],
            logic_blocks=distill_result["logic_blocks"],
            distilled_dimensions=distill_result["distilled_dimensions"],
        )

        blackboard_entries = self._build_blackboard_entries(
            distill_result=distill_result,
            critic_result=critic_result,
        )
        evidences = [
            retriever._evidence_from_blackboard_entry(entry)
            for entry in blackboard_entries
        ]
        selected = retriever._select_distillation_evidences(
            evidences=evidences,
            top_k=7,
        )
        retrieval_summary = retriever._structured_retrieval_summary(
            intent="distill_kol_logic",
            evidences=selected,
        )

        report = reporter.run(
            intent="distill_kol_logic",
            query="sample distill e2e",
            retrieval_summary=retrieval_summary,
            evidences=selected,
        )

        self.assertTrue(distill_result["logic_blocks"])
        self.assertTrue(distill_result["distilled_dimensions"])
        self.assertEqual(critic_result["scorecard"]["setup_label"], "quote_dominated")
        self.assertEqual(critic_result["scorecard"]["speaker_type_counts"]["author"], 2)
        self.assertEqual(critic_result["scorecard"]["speaker_type_counts"]["reply"], 2)
        self.assertEqual(critic_result["scorecard"]["speaker_type_counts"]["reported"], 2)
        self.assertIsNotNone(retrieval_summary)
        assert retrieval_summary is not None
        self.assertIn("speaker_type_counts={'reply': 2, 'reported': 1}", retrieval_summary)
        self.assertIn("review_types=['fallback_distillation']", retrieval_summary)
        self.assertIn("scorecard={'total_logic_blocks': 6", retrieval_summary)
        self.assertIn("'speaker_type_counts': {'author': 2, 'reply': 2, 'repost': 0, 'reported': 2, 'quoted': 0, 'uncertain': 0}", retrieval_summary)
        self.assertIn("'finding_types': ['fallback_distillation', 'low_confidence', 'quoted_attribution']", retrieval_summary)
        self.assertIn("scorecard", report)
        self.assertIsInstance(report["scorecard"], dict)
        self.assertIn("speaker_type_counts", report["scorecard"])
        self.assertIn("Scorecard:", report["conclusion"])
        self.assertIn("quote_dominated", report["conclusion"] or "")

    def test_complex_sample_folds_duplicate_claims_and_merges_dimension_aliases(self) -> None:
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "xueqiu_statuses_complex_sample.jsonl"

        distiller = DistillerService(UnavailableProviderStub())
        critic = CriticService()
        retriever = object.__new__(RetrieverService)
        reporter = ReporterService(UnavailableProviderStub())

        distill_result = distiller.run(
            query=f"distill kol from {fixture_path}",
            source_records=[
                {
                    "title": "complex distill request",
                    "content": str(fixture_path),
                    "source_type": "xueqiu_statuses_jsonl",
                    "path": str(fixture_path),
                }
            ],
        )

        critic_result = critic.run(
            distillation_summary=distill_result["summary"],
            logic_blocks=distill_result["logic_blocks"],
            distilled_dimensions=distill_result["distilled_dimensions"],
        )

        blackboard_entries = self._build_blackboard_entries(
            distill_result=distill_result,
            critic_result=critic_result,
        )
        evidences = [
            retriever._evidence_from_blackboard_entry(entry)
            for entry in blackboard_entries
        ]
        selected = retriever._select_distillation_evidences(
            evidences=evidences,
            top_k=7,
        )
        retrieval_summary = retriever._structured_retrieval_summary(
            intent="distill_kol_logic",
            evidences=selected,
        )
        report = reporter.run(
            intent="distill_kol_logic",
            query="complex distill e2e",
            retrieval_summary=retrieval_summary,
            evidences=selected,
        )

        dimension_names = {item["dimension_name"] for item in distill_result["distilled_dimensions"]}
        alias_hits = {
            alias
            for item in distill_result["distilled_dimensions"]
            for alias in item.get("alias_hits", [])
        }
        duplicate_folded_blocks = [
            item for item in distill_result["logic_blocks"]
            if "duplicate_claim_folded" in (item.get("risk_flags") or [])
        ]

        self.assertIn("AI", dimension_names)
        self.assertIn("创新药", dimension_names)
        self.assertIn("出海", dimension_names)
        self.assertIn("artificial intelligence", alias_hits)
        self.assertIn("innovation drug", alias_hits)
        self.assertIn("global expansion", alias_hits)
        self.assertTrue(duplicate_folded_blocks)
        self.assertEqual(critic_result["scorecard"]["speaker_type_counts"]["reply"], 1)
        self.assertEqual(critic_result["scorecard"]["speaker_type_counts"]["reported"], 1)
        self.assertGreaterEqual(critic_result["scorecard"]["speaker_type_counts"]["author"], 2)
        self.assertEqual(critic_result["scorecard"]["setup_label"], "mixed_attribution")
        self.assertIsNotNone(retrieval_summary)
        assert retrieval_summary is not None
        self.assertIn("block_type_counts={'claim': 2, 'catalyst': 1}", retrieval_summary)
        self.assertIn("'finding_types': ['fallback_distillation', 'low_confidence', 'quoted_attribution'", retrieval_summary)
        self.assertIsInstance(report["scorecard"], dict)
        self.assertIn("Scorecard:", report["conclusion"])


if __name__ == "__main__":
    unittest.main()
