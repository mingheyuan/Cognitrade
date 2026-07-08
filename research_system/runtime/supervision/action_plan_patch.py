from __future__ import annotations

from collections import deque

from research_system.schemas.planning import (
    ActionPlan,
    ActionPlanPatch,
    ActionSpec,
)


def apply_action_plan_patch(
    plan: ActionPlan,
    patch: ActionPlanPatch,
) -> ActionPlan:
    actions = {action.node_id: action.model_copy(deep=True) for action in plan.actions}
    order = [action.node_id for action in plan.actions]
    notes = list(plan.notes or [])
    dispatch_trace = dict(plan.dispatch_trace or {})
    applied_patch_ids = list(dispatch_trace.get("applied_patch_ids") or [])
    if patch.patch_id not in applied_patch_ids:
        applied_patch_ids.append(patch.patch_id)
    dispatch_trace["applied_patch_ids"] = applied_patch_ids

    for operation in patch.operations:
        if operation.op_type == "insert_action":
            assert operation.action is not None
            new_action = operation.action.model_copy(deep=True)
            if new_action.node_id in actions:
                raise ValueError(f"duplicate action plan node: {new_action.node_id}")
            actions[new_action.node_id] = new_action
            order.append(new_action.node_id)
        elif operation.op_type == "remove_action":
            _remove_action(actions=actions, order=order, target_node_id=str(operation.target_node_id or ""))
        elif operation.op_type == "rewire_dependency":
            _rewire_dependency(
                actions=actions,
                target_node_id=str(operation.target_node_id or ""),
                new_depends_on=list(operation.new_depends_on or []),
                remove_depends_on=list(operation.remove_depends_on or []),
            )
        elif operation.op_type == "replace_subgraph":
            _replace_subgraph(
                actions=actions,
                order=order,
                target_node_id=str(operation.target_node_id or ""),
                replacement_actions=[action.model_copy(deep=True) for action in operation.replacement_actions],
            )
        elif operation.op_type in {"rerun_from", "mark_terminal", "request_replan"}:
            detail = str(operation.target_node_id or "").strip() or str(operation.reason or "").strip() or operation.op_type
            notes.append(f"patch:{operation.op_type}:{detail}")
        else:
            raise ValueError(f"unsupported action patch operation: {operation.op_type}")

        if operation.reason:
            notes.append(f"patch_reason:{operation.op_type}:{operation.reason}")

    patched_actions = [actions[node_id] for node_id in order if node_id in actions]
    _validate_action_plan_actions(patched_actions)
    return plan.model_copy(
        update={
            "actions": patched_actions,
            "notes": _unique_notes([*notes, *list(patch.notes or [])]),
            "dispatch_trace": dispatch_trace,
        },
        deep=True,
    )


def _remove_action(
    *,
    actions: dict[str, ActionSpec],
    order: list[str],
    target_node_id: str,
) -> None:
    if target_node_id not in actions:
        raise ValueError(f"unknown action plan node: {target_node_id}")
    actions.pop(target_node_id)
    if target_node_id in order:
        order.remove(target_node_id)
    for node_id, action in list(actions.items()):
        if target_node_id not in action.depends_on:
            continue
        actions[node_id] = action.model_copy(
            update={
                "depends_on": [dependency_id for dependency_id in action.depends_on if dependency_id != target_node_id],
            }
        )


def _rewire_dependency(
    *,
    actions: dict[str, ActionSpec],
    target_node_id: str,
    new_depends_on: list[str],
    remove_depends_on: list[str],
) -> None:
    try:
        action = actions[target_node_id]
    except KeyError as exc:
        raise ValueError(f"unknown action plan node: {target_node_id}") from exc
    updated_depends_on = [dependency_id for dependency_id in action.depends_on if dependency_id not in set(remove_depends_on)]
    for dependency_id in new_depends_on:
        dependency_id = str(dependency_id or "").strip()
        if not dependency_id:
            continue
        if dependency_id == target_node_id:
            raise ValueError(f"action plan node cannot depend on itself: {target_node_id}")
        if dependency_id not in updated_depends_on:
            updated_depends_on.append(dependency_id)
    actions[target_node_id] = action.model_copy(update={"depends_on": updated_depends_on})


def _replace_subgraph(
    *,
    actions: dict[str, ActionSpec],
    order: list[str],
    target_node_id: str,
    replacement_actions: list[ActionSpec],
) -> None:
    if target_node_id not in actions:
        raise ValueError(f"unknown action plan node: {target_node_id}")
    if not replacement_actions:
        raise ValueError("replacement_subgraph requires at least one action")
    replacement_ids = [action.node_id for action in replacement_actions]
    duplicate_ids = {node_id for node_id in replacement_ids if replacement_ids.count(node_id) > 1}
    if duplicate_ids:
        raise ValueError(f"duplicate replacement action plan nodes: {sorted(duplicate_ids)}")

    downstream_node_ids = [
        node_id
        for node_id, action in actions.items()
        if node_id != target_node_id and target_node_id in action.depends_on
    ]
    target_index = order.index(target_node_id)
    actions.pop(target_node_id)
    order.remove(target_node_id)

    for offset, action in enumerate(replacement_actions):
        if action.node_id in actions:
            raise ValueError(f"duplicate action plan node: {action.node_id}")
        actions[action.node_id] = action
        order.insert(target_index + offset, action.node_id)

    replacement_sink_ids = _sink_action_ids(replacement_actions)
    for node_id in downstream_node_ids:
        action = actions[node_id]
        rewritten_depends_on: list[str] = []
        for dependency_id in action.depends_on:
            if dependency_id == target_node_id:
                for sink_node_id in replacement_sink_ids:
                    if sink_node_id not in rewritten_depends_on:
                        rewritten_depends_on.append(sink_node_id)
                continue
            if dependency_id not in rewritten_depends_on:
                rewritten_depends_on.append(dependency_id)
        actions[node_id] = action.model_copy(update={"depends_on": rewritten_depends_on})


def _sink_action_ids(actions: list[ActionSpec]) -> list[str]:
    action_ids = {action.node_id for action in actions}
    source_action_ids = {
        dependency_id
        for action in actions
        for dependency_id in action.depends_on
        if dependency_id in action_ids
    }
    sink_action_ids = [action.node_id for action in actions if action.node_id not in source_action_ids]
    return sink_action_ids or [actions[-1].node_id]


def _validate_action_plan_actions(actions: list[ActionSpec]) -> None:
    node_ids = [action.node_id for action in actions]
    unique_node_ids = set(node_ids)
    if len(node_ids) != len(unique_node_ids):
        raise ValueError("duplicate action plan node ids detected")
    for action in actions:
        for dependency_id in action.depends_on:
            if dependency_id not in unique_node_ids:
                raise ValueError(f"unknown action plan dependency: {dependency_id}")
            if dependency_id == action.node_id:
                raise ValueError(f"action plan node cannot depend on itself: {action.node_id}")
    indegree = {action.node_id: 0 for action in actions}
    adjacency: dict[str, list[str]] = {action.node_id: [] for action in actions}
    for action in actions:
        for dependency_id in action.depends_on:
            adjacency.setdefault(dependency_id, []).append(action.node_id)
            indegree[action.node_id] += 1
    frontier = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while frontier:
        node_id = frontier.popleft()
        visited += 1
        for downstream_node_id in adjacency.get(node_id, []):
            indegree[downstream_node_id] -= 1
            if indegree[downstream_node_id] == 0:
                frontier.append(downstream_node_id)
    if visited != len(actions):
        raise ValueError("action plan graph contains a cycle")


def _unique_notes(notes: list[str]) -> list[str]:
    unique: list[str] = []
    for note in notes:
        normalized = str(note or "").strip()
        if not normalized or normalized in unique:
            continue
        unique.append(normalized)
    return unique


__all__ = ["apply_action_plan_patch"]
