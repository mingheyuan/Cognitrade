from __future__ import annotations

from dataclasses import dataclass

from research_system.schemas.planning import ActionPlan, ActionSpec


@dataclass(frozen=True)
class DagValidationResult:
    valid: bool
    errors: list[str]


class ActionDagScheduler:
    def validate(self, plan: ActionPlan) -> DagValidationResult:
        action_map = self._action_map(plan.actions)
        errors: list[str] = []
        for action in plan.actions:
            for dependency in action.depends_on:
                if dependency not in action_map:
                    errors.append(f"missing_dependency:{action.node_id}->{dependency}")
        if errors:
            return DagValidationResult(valid=False, errors=errors)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                errors.append(f"cycle:{node_id}")
                return
            visiting.add(node_id)
            action = action_map[node_id]
            for dependency in action.depends_on:
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)

        for action in plan.actions:
            visit(action.node_id)
        return DagValidationResult(valid=not errors, errors=errors)

    def ready_actions(
        self,
        *,
        pending_actions: list[ActionSpec],
        completed_node_ids: list[str],
        skipped_node_ids: list[str] | None = None,
    ) -> list[ActionSpec]:
        completed = set(completed_node_ids)
        skipped = set(skipped_node_ids or [])
        ready: list[ActionSpec] = []
        for action in pending_actions:
            if all(dependency in completed or dependency in skipped for dependency in action.depends_on):
                ready.append(action)
        return ready

    def dependency_blocked_actions(
        self,
        *,
        pending_actions: list[ActionSpec],
        failed_node_ids: list[str],
    ) -> list[ActionSpec]:
        failed = set(failed_node_ids)
        if not failed:
            return []
        blocked: list[ActionSpec] = []
        for action in pending_actions:
            if any(dependency in failed for dependency in action.depends_on):
                blocked.append(action)
        return blocked

    def fanout_depth(self, plan: ActionPlan) -> dict[str, int]:
        action_map = self._action_map(plan.actions)
        depth_cache: dict[str, int] = {}

        def resolve(node_id: str) -> int:
            cached = depth_cache.get(node_id)
            if cached is not None:
                return cached
            action = action_map[node_id]
            if not action.depends_on:
                depth_cache[node_id] = 0
                return 0
            depth = max(resolve(dependency) for dependency in action.depends_on) + 1
            depth_cache[node_id] = depth
            return depth

        for node_id in action_map:
            resolve(node_id)
        return depth_cache

    def descendants(self, plan: ActionPlan, node_id: str) -> set[str]:
        action_map = self._action_map(plan.actions)
        if node_id not in action_map:
            raise KeyError(f"unknown_node:{node_id}")
        children: dict[str, set[str]] = {}
        for action in plan.actions:
            for dependency in action.depends_on:
                children.setdefault(dependency, set()).add(action.node_id)
        resolved: set[str] = set()

        def visit(current: str) -> None:
            for child_id in children.get(current, set()):
                if child_id in resolved:
                    continue
                resolved.add(child_id)
                visit(child_id)

        visit(node_id)
        return resolved

    @staticmethod
    def _action_map(actions: list[ActionSpec]) -> dict[str, ActionSpec]:
        action_map: dict[str, ActionSpec] = {}
        for action in actions:
            action_map[action.node_id] = action
        return action_map


__all__ = ["ActionDagScheduler", "DagValidationResult"]
