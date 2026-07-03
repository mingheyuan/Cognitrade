from __future__ import annotations

from research_system.runtime.storage.jsonl import read_jsonl
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorQdrantAdminSupport:
    def __init__(self, host) -> None:
        self.host = host

    def rebuild_qdrant(self) -> dict:
        qdrant_available = getattr(self.host.retriever, "qdrant_available", False)
        qdrant = getattr(self.host.retriever, "qdrant", None)
        qdrant_error = getattr(self.host.retriever, "qdrant_error", None)
        if not qdrant_available or qdrant is None:
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
            blackboard_path = run_dir / "blackboard.jsonl"
            if not blackboard_path.exists():
                skipped_runs.append(run_dir.name)
                continue

            indexed_this_run = 0
            for row in read_jsonl(blackboard_path):
                if row.get("kind") not in {"source_record", "verified_snapshot", "logic_block", "distilled_dimension", "review_finding"}:
                    skipped_entries += 1
                    continue
                try:
                    entry = BlackboardEntry.model_validate(row)
                except Exception:
                    skipped_entries += 1
                    continue
                qdrant.upsert_entry(entry)
                indexed_entries += 1
                indexed_this_run += 1

            if indexed_this_run > 0:
                indexed_runs += 1
            else:
                skipped_runs.append(run_dir.name)

        return {
            "status": "completed",
            "message": "Qdrant rebuilt from workspace runs",
            "collection_name": self.host.settings.qdrant_collection_name,
            "indexed_runs": indexed_runs,
            "indexed_entries": indexed_entries,
            "skipped_entries": skipped_entries,
            "skipped_runs": skipped_runs,
        }
