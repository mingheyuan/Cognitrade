from __future__ import annotations

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext


class OrchestratorMethodologySnapshotGrouping:
    def group_snapshots_by_symbol(self, context: RuntimeContext) -> dict[str, dict[str, dict]]:
        grouped: dict[str, dict[str, dict]] = {}
        for entry in context.blackboard.list_entries():
            if entry.kind != "verified_snapshot":
                continue
            symbol = str(entry.payload.get("symbol") or "").strip()
            snapshot_type = str(entry.payload.get("snapshot_type") or "").strip()
            if not symbol or not snapshot_type:
                continue
            grouped.setdefault(symbol, {})[snapshot_type] = entry.payload
        return grouped
