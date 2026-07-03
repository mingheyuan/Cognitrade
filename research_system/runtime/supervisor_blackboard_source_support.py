from __future__ import annotations

from research_system.schemas.runtime import BlackboardEntry


class SupervisorBlackboardSourceSupport:
    def source_records(self, blackboard_entries: list[BlackboardEntry]) -> list[BlackboardEntry]:
        return [entry for entry in blackboard_entries if entry.kind == "source_record"]

    def latest_source_record(
        self,
        blackboard_entries: list[BlackboardEntry],
        *,
        source_types: set[str],
    ) -> BlackboardEntry | None:
        source_records = self.source_records(blackboard_entries)
        return next(
            (
                entry
                for entry in source_records
                if str((entry.payload or {}).get("source_type") or "") in source_types
            ),
            None,
        )

    def review_summary_present(self, blackboard_entries: list[BlackboardEntry]) -> bool:
        return any(
            str((entry.payload or {}).get("source_type") or "") == "review_summary"
            for entry in self.source_records(blackboard_entries)
        )


__all__ = ["SupervisorBlackboardSourceSupport"]
