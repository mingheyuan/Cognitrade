from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.runtime.execution.core.execution_graph_compat import (
    action_plan_to_execution_graph,
)
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.planning import ActionPlan


def persist_execution_plan(*, context: Any, plan: ActionPlan) -> ExecutionGraph:
    graph = action_plan_to_execution_graph(plan)
    current = getattr(context, "execution_graph", None)
    if isinstance(current, ExecutionGraph) and current.graph_id == graph.graph_id:
        metadata = {
            **dict(current.metadata or {}),
            **dict(graph.metadata or {}),
            "legacy_patch_history": list(getattr(context, "action_plan_patches", []) or []),
        }
        graph = ExecutionGraph.model_validate(
            {
                **graph.model_dump(mode="python"),
                "revision": current.revision + 1,
                "metadata": metadata,
            }
        )
    context.execution_graph = graph
    write_json(context.paths.task_plan, plan.model_dump(mode="json"))
    write_json(_execution_graph_path(context.paths), graph.model_dump(mode="json"))
    return graph


def persist_execution_graph(
    *,
    context: Any,
    graph: ExecutionGraph,
    compatibility_plan: ActionPlan | None = None,
) -> ExecutionGraph:
    context.execution_graph = graph.model_copy(deep=True)
    write_json(_execution_graph_path(context.paths), graph.model_dump(mode="json"))
    if compatibility_plan is not None:
        write_json(context.paths.task_plan, compatibility_plan.model_dump(mode="json"))
    return context.execution_graph


def _execution_graph_path(paths: Any) -> Path:
    configured = getattr(paths, "execution_graph", None)
    if configured is not None:
        return Path(configured)
    return Path(paths.task_plan).with_name("execution_graph.json")


__all__ = ["persist_execution_graph", "persist_execution_plan"]
