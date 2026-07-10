from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.blackboard import Blackboard
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.runtime import BlackboardEntry


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

    def test_legacy_blackboard_entries_are_mirrored_as_artifacts(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            artifact_board = ArtifactBoard(root / "artifact_board.jsonl")
            blackboard = Blackboard(root / "blackboard.jsonl", artifact_board=artifact_board)
            blackboard.bind_thread("thread-1")
            entry = BlackboardEntry(
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                kind="retrieved_evidence",
                agent="retriever",
                phase="retrieve",
                payload={"evidences": [{"title": "Evidence"}]},
                source_refs=[{"type": "source", "id": "source-1"}],
                confidence=0.7,
            )

            blackboard.add(entry)

            artifacts = artifact_board.list_artifacts(artifact_type="retrieved_evidence")
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].content, entry.payload)
            self.assertEqual(artifacts[0].metadata["legacy_entry_id"], entry.entry_id)
            self.assertEqual(artifacts[0].producer.executor, "retriever")


if __name__ == "__main__":
    unittest.main()
