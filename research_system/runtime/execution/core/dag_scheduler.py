from __future__ import annotations

from dataclasses import dataclass

from research_system.runtime.execution.core.execution_graph import ExecutionGraphScheduler
from research_system.runtime.execution.core.execution_graph_compat import (
    action_frontier_to_execution_graph,
    action_plan_to_execution_graph,
)
from research_system.schemas.planning import ActionPlan, ActionSpec


@dataclass(frozen=True)
class DagValidationResult:
    valid: bool
    errors: list[str]


class ActionDagScheduler:
    def __init__(self) -> None:
        self.execution_graph_scheduler = ExecutionGraphScheduler()

    def validate(self, plan: ActionPlan) -> DagValidationResult:
        node_ids = {action.node_id for action in plan.actions}
        missing_dependencies = [
            f"missing_dependency:{action.node_id}->{dependency_id}"
            for action in plan.actions
            for dependency_id in action.depends_on
            if dependency_id not in node_ids
        ]
        if missing_dependencies:
            return DagValidationResult(valid=False, errors=missing_dependencies)
        try:
            graph = action_plan_to_execution_graph(plan)
        except ValueError as exc:
            if "dependency cycle" in str(exc):
                first_node_id = plan.actions[0].node_id if plan.actions else "unknown"
                return DagValidationResult(valid=False, errors=[f"cycle:{first_node_id}"])
            return DagValidationResult(valid=False, errors=[str(exc)])
        result = self.execution_graph_scheduler.validate(graph)
        return DagValidationResult(valid=result.valid, errors=result.errors)

    def ready_actions(
        self,
        *,
        pending_actions: list[ActionSpec],
        completed_node_ids: list[str],
        skipped_node_ids: list[str] | None = None,
    ) -> list[ActionSpec]:
        skipped = list(skipped_node_ids or [])
        graph = action_frontier_to_execution_graph(
            pending_actions,
            completed_node_ids=completed_node_ids,
            skipped_node_ids=skipped,
        )
        ready_ids = {
            node.node_id
            for node in self.execution_graph_scheduler.ready_leaf_nodes(
                graph,
                completed_node_ids=completed_node_ids,
                skipped_node_ids=skipped,
                active_node_ids=[action.node_id for action in pending_actions],
            )
        }
        return [action for action in pending_actions if action.node_id in ready_ids]

    def dependency_blocked_actions(
        self,
        *,
        pending_actions: list[ActionSpec],
        failed_node_ids: list[str],
    ) -> list[ActionSpec]:
        if not failed_node_ids:
            return []
        graph = action_frontier_to_execution_graph(
            pending_actions,
            failed_node_ids=failed_node_ids,
        )
        blocked_ids = {
            node.node_id
            for node in self.execution_graph_scheduler.blocked_leaf_nodes(
                graph,
                failed_node_ids=failed_node_ids,
                active_node_ids=[action.node_id for action in pending_actions],
            )
        }
        return [action for action in pending_actions if action.node_id in blocked_ids]

    def fanout_depth(self, plan: ActionPlan) -> dict[str, int]:
        return self.execution_graph_scheduler.fanout_depth(action_plan_to_execution_graph(plan))

    def descendants(self, plan: ActionPlan, node_id: str) -> set[str]:
        try:
            return self.execution_graph_scheduler.dependency_descendants(
                action_plan_to_execution_graph(plan),
                node_id,
            )
        except KeyError as exc:
            raise KeyError(f"unknown_node:{node_id}") from exc

    @staticmethod
    def _action_map(actions: list[ActionSpec]) -> dict[str, ActionSpec]:
        action_map: dict[str, ActionSpec] = {}
        for action in actions:
            action_map[action.node_id] = action
        return action_map


__all__ = ["ActionDagScheduler", "DagValidationResult"]
