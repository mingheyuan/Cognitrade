from __future__ import annotations

from collections import deque

from research_system.schemas.subagents import (
    SubagentGraphPatch,
    SubagentPlan,
    SubagentPlanNode,
)


def apply_subagent_graph_patch(
    plan: SubagentPlan,
    patch: SubagentGraphPatch,
) -> SubagentPlan:
    nodes = {node.node_id: node.model_copy(deep=True) for node in plan.nodes}
    order = [node.node_id for node in plan.nodes]
    notes = list(plan.notes or [])
    catalog_trace = dict(plan.catalog_trace or {})
    applied_patch_ids = list(catalog_trace.get("applied_patch_ids") or [])
    if patch.patch_id not in applied_patch_ids:
        applied_patch_ids.append(patch.patch_id)
    catalog_trace["applied_patch_ids"] = applied_patch_ids

    for operation in patch.operations:
        if operation.op_type == "insert_subagent":
            assert operation.node is not None
            new_node = operation.node.model_copy(deep=True)
            if new_node.node_id in nodes:
                raise ValueError(f"duplicate subagent plan node: {new_node.node_id}")
            nodes[new_node.node_id] = new_node
            order.append(new_node.node_id)
        elif operation.op_type == "remove_node":
            _remove_node(nodes=nodes, order=order, target_node_id=str(operation.target_node_id or ""))
        elif operation.op_type == "rewire_dependency":
            _rewire_dependency(
                nodes=nodes,
                target_node_id=str(operation.target_node_id or ""),
                new_depends_on=list(operation.new_depends_on or []),
                remove_depends_on=list(operation.remove_depends_on or []),
            )
        elif operation.op_type == "replace_subgraph":
            _replace_subgraph(
                nodes=nodes,
                order=order,
                target_node_id=str(operation.target_node_id or ""),
                replacement_nodes=[node.model_copy(deep=True) for node in operation.replacement_nodes],
            )
        elif operation.op_type in {"rerun_from", "mark_terminal", "request_replan"}:
            detail = str(operation.target_node_id or "").strip() or str(operation.reason or "").strip() or operation.op_type
            notes.append(f"patch:{operation.op_type}:{detail}")
        else:
            raise ValueError(f"unsupported graph patch operation: {operation.op_type}")

        if operation.reason:
            notes.append(f"patch_reason:{operation.op_type}:{operation.reason}")

    patched_nodes = [nodes[node_id] for node_id in order if node_id in nodes]
    _validate_subagent_plan_nodes(patched_nodes)
    return plan.model_copy(
        update={
            "nodes": patched_nodes,
            "notes": _unique_notes([*notes, *list(patch.notes or [])]),
            "catalog_trace": catalog_trace,
        },
        deep=True,
    )


def _remove_node(
    *,
    nodes: dict[str, SubagentPlanNode],
    order: list[str],
    target_node_id: str,
) -> None:
    if target_node_id not in nodes:
        raise ValueError(f"unknown subagent plan node: {target_node_id}")
    nodes.pop(target_node_id)
    if target_node_id in order:
        order.remove(target_node_id)
    for node_id, node in list(nodes.items()):
        if target_node_id not in node.depends_on:
            continue
        nodes[node_id] = node.model_copy(
            update={
                "depends_on": [dependency_id for dependency_id in node.depends_on if dependency_id != target_node_id],
            }
        )


def _rewire_dependency(
    *,
    nodes: dict[str, SubagentPlanNode],
    target_node_id: str,
    new_depends_on: list[str],
    remove_depends_on: list[str],
) -> None:
    try:
        node = nodes[target_node_id]
    except KeyError as exc:
        raise ValueError(f"unknown subagent plan node: {target_node_id}") from exc
    updated_depends_on = [dependency_id for dependency_id in node.depends_on if dependency_id not in set(remove_depends_on)]
    for dependency_id in new_depends_on:
        dependency_id = str(dependency_id or "").strip()
        if not dependency_id:
            continue
        if dependency_id == target_node_id:
            raise ValueError(f"subagent plan node cannot depend on itself: {target_node_id}")
        if dependency_id not in updated_depends_on:
            updated_depends_on.append(dependency_id)
    nodes[target_node_id] = node.model_copy(update={"depends_on": updated_depends_on})


def _replace_subgraph(
    *,
    nodes: dict[str, SubagentPlanNode],
    order: list[str],
    target_node_id: str,
    replacement_nodes: list[SubagentPlanNode],
) -> None:
    if target_node_id not in nodes:
        raise ValueError(f"unknown subagent plan node: {target_node_id}")
    if not replacement_nodes:
        raise ValueError("replacement_subgraph requires at least one node")
    replacement_ids = [node.node_id for node in replacement_nodes]
    duplicate_ids = {node_id for node_id in replacement_ids if replacement_ids.count(node_id) > 1}
    if duplicate_ids:
        raise ValueError(f"duplicate replacement subagent plan nodes: {sorted(duplicate_ids)}")

    downstream_node_ids = [
        node_id
        for node_id, node in nodes.items()
        if node_id != target_node_id and target_node_id in node.depends_on
    ]
    target_index = order.index(target_node_id)
    nodes.pop(target_node_id)
    order.remove(target_node_id)

    for offset, node in enumerate(replacement_nodes):
        if node.node_id in nodes:
            raise ValueError(f"duplicate subagent plan node: {node.node_id}")
        nodes[node.node_id] = node
        order.insert(target_index + offset, node.node_id)

    replacement_sink_ids = _sink_node_ids(replacement_nodes)
    for node_id in downstream_node_ids:
        node = nodes[node_id]
        rewritten_depends_on: list[str] = []
        for dependency_id in node.depends_on:
            if dependency_id == target_node_id:
                for sink_node_id in replacement_sink_ids:
                    if sink_node_id not in rewritten_depends_on:
                        rewritten_depends_on.append(sink_node_id)
                continue
            if dependency_id not in rewritten_depends_on:
                rewritten_depends_on.append(dependency_id)
        nodes[node_id] = node.model_copy(update={"depends_on": rewritten_depends_on})


def _sink_node_ids(nodes: list[SubagentPlanNode]) -> list[str]:
    source_node_ids = {
        dependency_id
        for node in nodes
        for dependency_id in node.depends_on
        if dependency_id in {candidate.node_id for candidate in nodes}
    }
    sink_node_ids = [node.node_id for node in nodes if node.node_id not in source_node_ids]
    return sink_node_ids or [nodes[-1].node_id]


def _validate_subagent_plan_nodes(nodes: list[SubagentPlanNode]) -> None:
    node_ids = [node.node_id for node in nodes]
    unique_node_ids = set(node_ids)
    if len(node_ids) != len(unique_node_ids):
        raise ValueError("duplicate subagent plan node ids detected")
    for node in nodes:
        for dependency_id in node.depends_on:
            if dependency_id not in unique_node_ids:
                raise ValueError(f"unknown subagent plan dependency: {dependency_id}")
            if dependency_id == node.node_id:
                raise ValueError(f"subagent plan node cannot depend on itself: {node.node_id}")
    indegree = {node.node_id: 0 for node in nodes}
    adjacency: dict[str, list[str]] = {node.node_id: [] for node in nodes}
    for node in nodes:
        for dependency_id in node.depends_on:
            adjacency.setdefault(dependency_id, []).append(node.node_id)
            indegree[node.node_id] += 1
    frontier = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while frontier:
        node_id = frontier.popleft()
        visited += 1
        for downstream_node_id in adjacency.get(node_id, []):
            indegree[downstream_node_id] -= 1
            if indegree[downstream_node_id] == 0:
                frontier.append(downstream_node_id)
    if visited != len(nodes):
        raise ValueError("subagent plan graph contains a cycle")


def _unique_notes(notes: list[str]) -> list[str]:
    unique: list[str] = []
    for note in notes:
        normalized = str(note or "").strip()
        if not normalized or normalized in unique:
            continue
        unique.append(normalized)
    return unique


__all__ = ["apply_subagent_graph_patch"]
