from __future__ import annotations

from research_system.schemas.runtime import BlackboardEntry


class SupervisorBlackboardSnapshotSupport:
    def snapshot_types(self, blackboard_entries: list[BlackboardEntry]) -> set[str]:
        return {
            str((entry.payload or {}).get("snapshot_type") or "")
            for entry in blackboard_entries
            if entry.kind == "verified_snapshot"
        }

    def latest_snapshot(
        self,
        blackboard_entries: list[BlackboardEntry],
        *,
        snapshot_type: str,
    ) -> BlackboardEntry | None:
        return next(
            (
                entry
                for entry in reversed(blackboard_entries)
                if entry.kind == "verified_snapshot"
                and str((entry.payload or {}).get("snapshot_type") or "") == snapshot_type
            ),
            None,
        )

    def has_any_snapshot(self, blackboard_entries: list[BlackboardEntry], *, snapshot_types: set[str]) -> bool:
        return any(
            entry.kind == "verified_snapshot"
            and str((entry.payload or {}).get("snapshot_type") or "") in snapshot_types
            for entry in blackboard_entries
        )


__all__ = ["SupervisorBlackboardSnapshotSupport"]
