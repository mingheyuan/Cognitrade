from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.services.memory.layers import LayeredMemoryStore, MemoryPromotionError
from research_system.schemas.memory import MemoryEvaluation


class LayeredMemoryTests(unittest.TestCase):
    def test_requires_gated_episode_and_evaluated_one_way_promotions(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            store = LayeredMemoryStore(Path(temporary_directory))
            with self.assertRaisesRegex(MemoryPromotionError, "ungated"):
                store.add_episode(
                    domain_id="legal_contract",
                    memory_type="legal_review_report",
                    content={"finding": "risk"},
                    source_run_id="run-1",
                    source_artifact_ids=["artifact-1"],
                    gated=False,
                )

            episode = store.add_episode(
                domain_id="legal_contract",
                memory_type="legal_review_report",
                content={"finding": "risk"},
                source_run_id="run-1",
                source_artifact_ids=["artifact-1"],
                gated=True,
                gate_evaluations=[
                    {
                        "gate_id": "legal.citation_required",
                        "artifact_id": "artifact-1",
                        "verdict": "pass",
                        "reason": "citations present",
                    }
                ],
            )
            with self.assertRaisesRegex(MemoryPromotionError, "scored passing"):
                store.promote(
                    memory_id=episode.memory_id,
                    target_layer="domain",
                    evaluation=MemoryEvaluation(
                        evaluation_id="eval-fail",
                        evaluator="reviewer",
                        verdict="fail",
                        score=0.9,
                        evidence_refs=["artifact-1"],
                    ),
                )

            domain = store.promote(
                memory_id=episode.memory_id,
                target_layer="domain",
                evaluation=MemoryEvaluation(
                    evaluation_id="eval-domain",
                    evaluator="reviewer",
                    verdict="pass",
                    score=0.8,
                    evidence_refs=["artifact-1"],
                ),
            )
            with self.assertRaisesRegex(MemoryPromotionError, "cannot be promoted"):
                store.promote(
                    memory_id=episode.memory_id,
                    target_layer="domain",
                    evaluation=MemoryEvaluation(
                        evaluation_id="eval-duplicate",
                        evaluator="reviewer",
                        verdict="pass",
                        score=0.9,
                        evidence_refs=["artifact-1"],
                    ),
                )
            policy = store.promote(
                memory_id=domain.memory_id,
                target_layer="policy",
                evaluation=MemoryEvaluation(
                    evaluation_id="eval-policy",
                    evaluator="benchmark",
                    verdict="pass",
                    score=0.9,
                    evidence_refs=[domain.memory_id],
                ),
            )

            self.assertEqual(domain.layer, "domain")
            self.assertEqual(policy.layer, "policy")
            self.assertEqual(policy.parent_memory_ids, [domain.memory_id])
            self.assertEqual(len(store.list(domain_id="legal_contract")), 3)


if __name__ == "__main__":
    unittest.main()
