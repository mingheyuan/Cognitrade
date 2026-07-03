from __future__ import annotations

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext


def collect_screening_inputs(context: RuntimeContext) -> dict[str, list[dict]]:
    return {
        "source_payloads": [
            entry.payload
            for entry in context.blackboard.list_entries()
            if entry.kind == "source_record"
        ],
        "snapshot_payloads": [
            entry.payload
            for entry in context.blackboard.list_entries()
            if entry.kind == "verified_snapshot"
        ],
    }
