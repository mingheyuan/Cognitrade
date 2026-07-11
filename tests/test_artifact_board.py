from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord


class ArtifactBoardTests(unittest.TestCase):
    def test_add_and_query_typed_artifacts(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            board = ArtifactBoard(Path(temporary_directory) / "artifacts.jsonl")
            board.bind_thread("thread-1")
            artifact = board.add(
                ArtifactRecord(
                    run_id="run-1",
                    thread_id="thread-1",
                    request_id="request-1",
                    artifact_type="finding",
                    schema_version="legal.finding.v1",
                    producer=ArtifactProducer(
                        executor="contract_reviewer",
                        capability="contract.risk.review",
                        domain_id="legal",
                    ),
                    content={"risk": "termination"},
                    confidence=0.8,
                )
            )

            rows = board.list_artifacts(
                artifact_type="finding",
                capability="contract.risk.review",
            )

            self.assertEqual(rows, [artifact])
            self.assertEqual(board.get(artifact.artifact_id), artifact)

    def test_explicit_global_queries_bypass_bound_thread_and_batches_validate_before_write(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            board = ArtifactBoard(Path(temporary_directory) / "artifacts.jsonl")
            first = ArtifactRecord(
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                artifact_type="finding",
                producer=ArtifactProducer(
                    executor="reviewer",
                    capability="review.finding",
                ),
                content={"value": "one"},
                source_refs=[{"type": "test", "id": "one"}],
            )
            second = first.model_copy(
                deep=True,
                update={
                    "artifact_id": "artifact-two",
                    "run_id": "run-2",
                    "thread_id": "thread-2",
                    "content": {"value": "two"},
                },
            )
            board.add_many([first, second])
            board.bind_thread("thread-1")

            self.assertEqual(len(board.list_artifacts()), 1)
            self.assertEqual(len(board.list_artifacts(thread_id=None)), 2)
            self.assertIsNotNone(board.get("artifact-two"))

            invalid = second.model_copy(
                deep=True,
                update={
                    "artifact_id": "artifact-invalid",
                    "parent_artifact_ids": ["does-not-exist"],
                },
            )
            with self.assertRaisesRegex(ValueError, "unknown parent artifact"):
                board.add_many([invalid])
            self.assertIsNone(board.get("artifact-invalid"))

    def test_active_queries_restore_patch_invalidations_from_run_snapshot(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            board = ArtifactBoard(root / "artifact_board.jsonl")
            artifact = board.add(ArtifactRecord(
                artifact_id="stale-artifact",
                artifact_type="fact",
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                producer=ArtifactProducer(executor="worker", capability="fact", node_id="node-1"),
                content={"value": "old"},
                source_refs=[{"type": "source", "id": "source-1"}],
                confidence=0.9,
            ))
            write_json(root / "execution_snapshot.json", {"invalidated_artifact_ids": [artifact.artifact_id]})

            self.assertEqual(
                board.list_artifacts(run_id="run-1", status="active"),
                [],
            )
            self.assertEqual(board.get(artifact.artifact_id), artifact)

if __name__ == "__main__":
    unittest.main()
