from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.core.artifact_board import ArtifactBoard
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

if __name__ == "__main__":
    unittest.main()
