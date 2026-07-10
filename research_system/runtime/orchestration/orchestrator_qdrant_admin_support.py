from __future__ import annotations

from research_system.adapters.storage.qdrant_adapter import QdrantAdapter, QdrantUnavailableError
from research_system.runtime.core.artifact_board import ArtifactBoard


class OrchestratorQdrantAdminSupport:
    def __init__(self, host) -> None:
        self.host = host

    def rebuild_qdrant(self) -> dict:
        qdrant = getattr(self.host, "qdrant", None)
        qdrant_error = getattr(self.host, "qdrant_error", None)
        if qdrant is None:
            try:
                qdrant = QdrantAdapter(
                    self.host.settings,
                    embedding_provider=getattr(self.host, "llm_provider", None),
                )
                self.host.qdrant = qdrant
                self.host.qdrant_error = None
            except QdrantUnavailableError as exc:
                qdrant_error = str(exc)
                self.host.qdrant_error = qdrant_error
        if qdrant is None or not qdrant.available:
            return {
                "status": "skipped",
                "message": "Qdrant unavailable",
                "qdrant_error": qdrant_error,
            }

        qdrant.reset_collection()

        indexed_entries = 0
        skipped_entries = 0
        indexed_runs = 0
        skipped_runs: list[str] = []

        for run_dir in sorted(self.host.settings.runs_root.iterdir()):
            if not run_dir.is_dir():
                continue
            artifact_board_path = run_dir / "artifact_board.jsonl"
            if not artifact_board_path.exists():
                skipped_runs.append(run_dir.name)
                continue

            indexed_this_run = 0
            board = ArtifactBoard(artifact_board_path)
            for artifact in board.list_artifacts(thread_id=None):
                if artifact.artifact_type == "user_request":
                    skipped_entries += 1
                    continue
                qdrant.upsert_artifact(artifact)
                indexed_entries += 1
                indexed_this_run += 1

            if indexed_this_run > 0:
                indexed_runs += 1
            else:
                skipped_runs.append(run_dir.name)

        return {
            "status": "completed",
            "message": "Qdrant rebuilt from Agent OS artifact boards",
            "collection_name": self.host.settings.qdrant_collection_name,
            "indexed_runs": indexed_runs,
            "indexed_entries": indexed_entries,
            "skipped_entries": skipped_entries,
            "skipped_runs": skipped_runs,
        }
