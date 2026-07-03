from __future__ import annotations

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext


def collect_blackboard_entries_json(context: RuntimeContext) -> list[dict]:
    return [entry.model_dump(mode="json") for entry in context.blackboard.list_entries()]
