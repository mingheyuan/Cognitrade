from __future__ import annotations

import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.storage.jsonl import read_jsonl
from research_system.services.memory.layers import LayeredMemoryStore, MemoryPromotionError
from research_system.schemas.memory import MemoryEvaluation, utc_now


class _MemoryRetrievalBackend:
    def __init__(self) -> None:
        self.records = {}

    def upsert_memory(self, memory) -> None:
        self.records[memory.memory_id] = memory

    def search_memory(self, *, query_text, top_k, metadata_filters=None):
        rows = list(self.records.values())
        rows.sort(key=lambda row: 0 if "cashflow" in str(row.content).lower() else 1)
        return [
            {
                "id": row.memory_id,
                "payload": {"memory_id": row.memory_id},
            }
            for row in rows[:top_k]
        ]


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

    def test_scoped_memory_is_only_visible_to_the_matching_identity(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            store = LayeredMemoryStore(Path(temporary_directory))
            record = store.add_episode(
                domain_id="finance_ashare",
                memory_type="research_report",
                content={"summary": "tenant-private case"},
                source_run_id="run-1",
                source_artifact_ids=["artifact-1"],
                gated=True,
                scope="user",
                tenant_id="tenant-a",
                user_id="user-a",
            )

            self.assertEqual(
                store.list(tenant_id="tenant-a", user_id="user-a"),
                [record],
            )
            self.assertEqual(store.list(tenant_id="tenant-a", user_id="user-b"), [])
            self.assertEqual(store.list(tenant_id="tenant-b", user_id="user-a"), [])
            self.assertEqual(store.list(), [])

    def test_query_ranks_relevant_verified_memory_instead_of_recent_rows(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            store = LayeredMemoryStore(Path(temporary_directory))
            first = store.add_episode(
                domain_id="finance_ashare",
                memory_type="research_case",
                content={"summary": "cashflow resilience and valuation evidence"},
                source_run_id="run-1",
                source_artifact_ids=["artifact-1"],
                gated=True,
            )
            store.add_episode(
                domain_id="finance_ashare",
                memory_type="research_case",
                content={"summary": "unrelated sector observation"},
                source_run_id="run-2",
                source_artifact_ids=["artifact-2"],
                gated=True,
            )

            matches = store.list(query="cashflow valuation", status="verified")

            self.assertEqual([item.memory_id for item in matches], [first.memory_id])

    def test_optional_vector_backend_ranks_indexed_memory_before_lexical_fallback(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            backend = _MemoryRetrievalBackend()
            store = LayeredMemoryStore(
                Path(temporary_directory),
                retrieval_backend=backend,
            )
            cashflow = store.add_episode(
                domain_id="finance_ashare",
                memory_type="research_case",
                content={"summary": "cashflow resilience case"},
                source_run_id="run-1",
                source_artifact_ids=["artifact-1"],
                gated=True,
            )
            unrelated = store.add_episode(
                domain_id="finance_ashare",
                memory_type="research_case",
                content={"summary": "sector observation"},
                source_run_id="run-2",
                source_artifact_ids=["artifact-2"],
                gated=True,
            )

            matches = store.list(query="cashflow", status="verified")

            self.assertEqual([item.memory_id for item in matches], [cashflow.memory_id, unrelated.memory_id])

    def test_expiration_is_persisted_as_an_audited_state_transition(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            store = LayeredMemoryStore(Path(temporary_directory))
            record = store.add_episode(
                domain_id="finance_ashare",
                memory_type="research_case",
                content={"summary": "temporary case"},
                source_run_id="run-1",
                source_artifact_ids=["artifact-1"],
                gated=True,
                expires_at=utc_now() - timedelta(seconds=1),
            )

            expired = store.expire_records()

            self.assertEqual([item.memory_id for item in expired], [record.memory_id])
            current = store.get(record.memory_id)
            self.assertIsNotNone(current)
            self.assertEqual(current.status, "expired")
            self.assertIn(
                "memory_expired",
                [row["action"] for row in read_jsonl(store.audit_path)],
            )


if __name__ == "__main__":
    unittest.main()
