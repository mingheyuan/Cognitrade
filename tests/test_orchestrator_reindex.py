from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.jsonl import append_jsonl
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.schemas.runtime import BlackboardEntry


class FakeQdrant:
    def __init__(self) -> None:
        self.reset_called = False
        self.entries: list[BlackboardEntry] = []

    def reset_collection(self) -> None:
        self.reset_called = True

    def upsert_entry(self, entry: BlackboardEntry) -> None:
        self.entries.append(entry)


class OrchestratorReindexTests(unittest.TestCase):
    def test_rebuild_qdrant_replays_supported_blackboard_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir) / "runs"
            self._write_run(
                runs_root / "run-a",
                entries=[
                    BlackboardEntry(
                        run_id="run-a",
                        request_id="req-a",
                        kind="source_record",
                        agent="collector",
                        phase="collect",
                        payload={
                            "title": "sector_research input query",
                            "content": "最近创新药板块怎么样",
                            "source_type": "user_query",
                        },
                        source_refs=[{"type": "request", "request_id": "req-a", "run_id": "run-a"}],
                    ),
                    BlackboardEntry(
                        run_id="run-a",
                        request_id="req-a",
                        kind="verified_snapshot",
                        agent="collector",
                        phase="collect",
                        payload={
                            "snapshot_type": "sector",
                            "sector": "创新药",
                            "snapshot_date": "2026-06-28",
                            "facts": {},
                        },
                        source_refs=[{"type": "skill", "skill": "sector_snapshot_skill", "run_id": "run-a"}],
                    ),
                    BlackboardEntry(
                        run_id="run-a",
                        request_id="req-a",
                        kind="logic_block",
                        agent="retriever",
                        phase="retrieve",
                        payload={
                            "block_id": "logic-1",
                            "block_type": "claim",
                            "source_record_id": "src-1",
                            "source_span": {"start_line": 1, "end_line": 1, "excerpt": "test"},
                            "speaker_type": "author",
                            "text": "claim text",
                            "confidence": 0.7,
                        },
                    ),
                    BlackboardEntry(
                        run_id="run-a",
                        request_id="req-a",
                        kind="distilled_dimension",
                        agent="distiller",
                        phase="distill",
                        payload={
                            "dimension_id": "dim-1",
                            "dimension_name": "internet",
                            "dimension_category": "industry",
                            "alias_hits": ["internet"],
                            "supported_claim_block_ids": ["logic-1"],
                            "evidence_block_ids": [],
                            "is_new_candidate": True,
                            "confidence": 0.6,
                            "normalization_notes": [],
                        },
                    ),
                    BlackboardEntry(
                        run_id="run-a",
                        request_id="req-a",
                        kind="review_finding",
                        agent="critic",
                        phase="review",
                        payload={
                            "finding_id": "finding-1",
                            "target_entry_id": "logic-1",
                            "target_kind": "logic_block",
                            "finding_type": "low_confidence",
                            "severity": "medium",
                            "verdict": "needs_review",
                            "reason": "confidence is low",
                        },
                    ),
                ],
            )
            self._write_run(
                runs_root / "run-b",
                entries=[
                    BlackboardEntry(
                        run_id="run-b",
                        request_id="req-b",
                        kind="verified_snapshot",
                        agent="collector",
                        phase="collect",
                        payload={
                            "snapshot_type": "market",
                            "symbol": "000001",
                            "snapshot_date": "2026-06-28",
                            "facts": {},
                        },
                        source_refs=[{"type": "skill", "skill": "market_snapshot_skill", "run_id": "run-b"}],
                    ),
                ],
            )
            (runs_root / "run-c").mkdir(parents=True, exist_ok=True)

            fake_qdrant = FakeQdrant()
            orchestrator = object.__new__(ResearchOrchestrator)
            orchestrator.settings = type(
                "SettingsStub",
                (),
                {
                    "runs_root": runs_root,
                    "qdrant_collection_name": "research_evidence",
                },
            )()
            orchestrator.retriever = type(
                "RetrieverStub",
                (),
                {
                    "qdrant_available": True,
                    "qdrant": fake_qdrant,
                    "qdrant_error": None,
                },
            )()

            result = ResearchOrchestrator.rebuild_qdrant(orchestrator)

            self.assertTrue(fake_qdrant.reset_called)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["indexed_runs"], 2)
            self.assertEqual(result["indexed_entries"], 6)
            self.assertEqual(result["skipped_entries"], 0)
            self.assertIn("run-c", result["skipped_runs"])
            self.assertEqual(len(fake_qdrant.entries), 6)
            self.assertTrue(all(isinstance(entry, BlackboardEntry) for entry in fake_qdrant.entries))

    def _write_run(self, run_dir: Path, entries: list[BlackboardEntry]) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        blackboard_path = run_dir / "blackboard.jsonl"
        for entry in entries:
            append_jsonl(blackboard_path, entry.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
