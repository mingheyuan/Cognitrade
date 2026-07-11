from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

from research_system.schemas.execution_graph import (
    ExecutionGraph,
    ExecutionGraphPatch,
    ExecutionNode,
)


@dataclass(frozen=True)
class ExecutionGraphValidationResult:
    valid: bool
    errors: list[str]


class ExecutionGraphScheduler:
    def validate(self, graph: ExecutionGraph) -> ExecutionGraphValidationResult:
        try:
            ExecutionGraph.model_validate(graph.model_dump(mode="python"))
        except ValueError as exc:
            return ExecutionGraphValidationResult(valid=False, errors=[str(exc)])
        return ExecutionGraphValidationResult(valid=True, errors=[])

    def ready_leaf_nodes(
        self,
        graph: ExecutionGraph,
        *,
        completed_node_ids: Iterable[str] = (),
        skipped_node_ids: Iterable[str] = (),
        failed_node_ids: Iterable[str] = (),
        active_node_ids: Iterable[str] | None = None,
    ) -> list[ExecutionNode]:
        completed = set(completed_node_ids)
        skipped = set(skipped_node_ids)
        failed = set(failed_node_ids)
        active = set(active_node_ids) if active_node_ids is not None else None
        node_map = self.node_map(graph)
        resolved = completed | skipped
        ready: list[ExecutionNode] = []
        for node in graph.nodes:
            if node.node_kind != "subagent":
                continue
            if active is not None and node.node_id not in active:
                continue
            if node.node_id in resolved or node.node_id in failed:
                continue
            dependencies = self.effective_dependencies(graph, node.node_id)
            failed_dependencies = [
                dependency_id
                for dependency_id in dependencies
                if self._dependency_failed(
                    graph,
                    dependency_id,
                    failed=failed,
                )
            ]
            if failed_dependencies and not node.allow_partial_dependencies:
                continue
            if all(
                self._dependency_resolved(
                    graph,
                    dependency_id,
                    resolved=resolved,
                    failed=failed,
                    allow_failed=node.allow_partial_dependencies,
                )
                for dependency_id in dependencies
            ):
                ready.append(node_map[node.node_id])
        return ready

    def blocked_leaf_nodes(
        self,
        graph: ExecutionGraph,
        *,
        failed_node_ids: Iterable[str],
        active_node_ids: Iterable[str] | None = None,
    ) -> list[ExecutionNode]:
        failed = set(failed_node_ids)
        active = set(active_node_ids) if active_node_ids is not None else None
        blocked: list[ExecutionNode] = []
        for node in graph.nodes:
            if node.node_kind != "subagent" or node.allow_partial_dependencies:
                continue
            if active is not None and node.node_id not in active:
                continue
            if any(
                self._dependency_failed(graph, dependency_id, failed=failed)
                for dependency_id in self.effective_dependencies(graph, node.node_id)
            ):
                blocked.append(node)
        return blocked

    def expandable_compounds(
        self,
        graph: ExecutionGraph,
        *,
        completed_node_ids: Iterable[str] = (),
        skipped_node_ids: Iterable[str] = (),
        failed_node_ids: Iterable[str] = (),
    ) -> list[ExecutionNode]:
        children = self.children_by_parent(graph)
        completed = set(completed_node_ids)
        skipped = set(skipped_node_ids)
        failed = set(failed_node_ids)
        resolved = completed | skipped
        ready: list[ExecutionNode] = []
        for node in graph.nodes:
            if (
                node.node_kind != "compound"
                or node.expansion_policy != "lazy"
                or children.get(node.node_id)
            ):
                continue
            if node.node_id in resolved or node.node_id in failed:
                continue
            dependencies = self.effective_dependencies(graph, node.node_id)
            if all(
                self._dependency_resolved(
                    graph,
                    dependency_id,
                    resolved=resolved,
                    failed=failed,
                    allow_failed=False,
                )
                for dependency_id in dependencies
            ):
                ready.append(node)
        return ready

    def effective_dependencies(self, graph: ExecutionGraph, node_id: str) -> list[str]:
        node_map = self.node_map(graph)
        node = node_map[node_id]
        dependencies: list[str] = []
        lineage: list[ExecutionNode] = []
        current: ExecutionNode | None = node
        while current is not None:
            lineage.append(current)
            current = node_map.get(current.parent_node_id) if current.parent_node_id else None
        for lineage_node in reversed(lineage):
            for dependency_id in lineage_node.depends_on:
                if dependency_id not in dependencies:
                    dependencies.append(dependency_id)
        return dependencies

    def descendants(self, graph: ExecutionGraph, node_id: str) -> set[str]:
        node_map = self.node_map(graph)
        if node_id not in node_map:
            raise KeyError(f"unknown execution graph node: {node_id}")
        children = self.children_by_parent(graph)
        resolved: set[str] = set()
        stack = list(children.get(node_id, []))
        while stack:
            child_id = stack.pop()
            if child_id in resolved:
                continue
            resolved.add(child_id)
            stack.extend(children.get(child_id, []))
        return resolved

    def dependency_descendants(self, graph: ExecutionGraph, node_id: str) -> set[str]:
        node_map = self.node_map(graph)
        if node_id not in node_map:
            raise KeyError(f"unknown execution graph node: {node_id}")
        downstream: dict[str, set[str]] = {}
        for node in graph.nodes:
            for dependency_id in node.depends_on:
                downstream.setdefault(dependency_id, set()).add(node.node_id)
        resolved: set[str] = set()
        stack = list(downstream.get(node_id, set()))
        while stack:
            downstream_id = stack.pop()
            if downstream_id in resolved:
                continue
            resolved.add(downstream_id)
            stack.extend(downstream.get(downstream_id, set()))
        return resolved

    def fanout_depth(self, graph: ExecutionGraph) -> dict[str, int]:
        node_map = self.node_map(graph)
        cache: dict[str, int] = {}

        def resolve(node_id: str) -> int:
            if node_id in cache:
                return cache[node_id]
            dependencies = self.effective_dependencies(graph, node_id)
            if not dependencies:
                cache[node_id] = 0
            else:
                cache[node_id] = max(resolve(dependency_id) for dependency_id in dependencies) + 1
            return cache[node_id]

        for node_id in node_map:
            resolve(node_id)
        return cache

    @staticmethod
    def node_map(graph: ExecutionGraph) -> dict[str, ExecutionNode]:
        return {node.node_id: node for node in graph.nodes}

    @staticmethod
    def children_by_parent(graph: ExecutionGraph) -> dict[str, list[str]]:
        children: dict[str, list[str]] = {}
        for node in graph.nodes:
            if node.parent_node_id:
                children.setdefault(node.parent_node_id, []).append(node.node_id)
        return children

    def _dependency_resolved(
        self,
        graph: ExecutionGraph,
        dependency_id: str,
        *,
        resolved: set[str],
        failed: set[str],
        allow_failed: bool,
    ) -> bool:
        if dependency_id in resolved:
            return True
        if allow_failed and dependency_id in failed:
            return True
        node = self.node_map(graph)[dependency_id]
        if node.node_kind == "subagent":
            return False
        leaf_ids = self._leaf_descendant_ids(graph, dependency_id)
        if not leaf_ids:
            return False
        allowed = resolved | (failed if allow_failed else set())
        return leaf_ids.issubset(allowed)

    def _dependency_failed(
        self,
        graph: ExecutionGraph,
        dependency_id: str,
        *,
        failed: set[str],
    ) -> bool:
        if dependency_id in failed:
            return True
        node = self.node_map(graph)[dependency_id]
        if node.node_kind == "subagent":
            return False
        return bool(self._leaf_descendant_ids(graph, dependency_id) & failed)

    def _leaf_descendant_ids(self, graph: ExecutionGraph, node_id: str) -> set[str]:
        node_map = self.node_map(graph)
        return {
            descendant_id
            for descendant_id in self.descendants(graph, node_id)
            if node_map[descendant_id].node_kind == "subagent"
        }


def apply_execution_graph_patch(
    graph: ExecutionGraph,
    patch: ExecutionGraphPatch,
) -> ExecutionGraph:
    if patch.base_revision != graph.revision:
        raise ValueError(
            f"stale execution graph patch: base={patch.base_revision}, current={graph.revision}"
        )
    nodes = {node.node_id: node.model_copy(deep=True) for node in graph.nodes}
    order = [node.node_id for node in graph.nodes]
    notes = list(graph.notes or [])
    reset_ids: set[str] = set()

    for operation in patch.operations:
        target_node_id = str(operation.target_node_id or "")
        if operation.op_type in {
            "remove_node",
            "rewire_dependency",
            "replace_subgraph",
            "rerun_from",
        } and target_node_id in nodes:
            reset_ids.add(target_node_id)
            reset_ids.update(
                _dependency_descendants_from_nodes(nodes, target_node_id)
            )
            if operation.op_type in {"replace_subgraph", "rerun_from"}:
                reset_ids.update(_descendants_from_nodes(nodes, target_node_id))
        if operation.op_type == "insert_node":
            assert operation.node is not None
            if operation.node.node_id in nodes:
                raise ValueError(f"duplicate execution graph node: {operation.node.node_id}")
            nodes[operation.node.node_id] = operation.node.model_copy(deep=True)
            order.append(operation.node.node_id)
        elif operation.op_type == "remove_node":
            _remove_execution_node(nodes, order, str(operation.target_node_id or ""))
        elif operation.op_type == "rewire_dependency":
            _rewire_execution_node(
                nodes,
                str(operation.target_node_id or ""),
                new_depends_on=operation.new_depends_on,
                remove_depends_on=operation.remove_depends_on,
            )
        elif operation.op_type == "replace_subgraph":
            _replace_execution_subgraph(
                nodes,
                order,
                str(operation.target_node_id or ""),
                [node.model_copy(deep=True) for node in operation.replacement_nodes],
            )
        elif operation.op_type == "rerun_from":
            _reset_execution_subgraph(nodes, target_node_id)
            detail = str(operation.target_node_id or operation.op_type)
            notes.append(f"patch:{operation.op_type}:{detail}")
        elif operation.op_type == "mark_terminal":
            target_node_id = str(operation.target_node_id or "")
            if target_node_id not in nodes:
                raise ValueError(f"unknown execution graph node: {target_node_id}")
            status = str(operation.metadata.get("status") or "skipped")
            if status not in {"completed", "skipped", "failed", "cancelled"}:
                raise ValueError(f"invalid terminal execution node status: {status}")
            nodes[target_node_id] = nodes[target_node_id].model_copy(update={"status": status})
            notes.append(f"patch:{operation.op_type}:{target_node_id}:{status}")
        elif operation.op_type == "request_replan":
            detail = str(operation.target_node_id or operation.reason or operation.op_type)
            notes.append(f"patch:{operation.op_type}:{detail}")
        else:  # pragma: no cover - protected by schema literal
            raise ValueError(f"unsupported execution graph patch operation: {operation.op_type}")
        if operation.reason:
            notes.append(f"patch_reason:{operation.op_type}:{operation.reason}")

    _reset_execution_nodes(nodes, reset_ids)

    metadata = dict(graph.metadata or {})
    patch_history = list(metadata.get("patch_history") or [])
    patch_history.append(patch.model_dump(mode="json"))
    metadata["patch_history"] = patch_history
    return ExecutionGraph.model_validate(
        {
            **graph.model_dump(mode="python"),
            "revision": graph.revision + 1,
            "nodes": [nodes[node_id].model_dump(mode="python") for node_id in order if node_id in nodes],
            "notes": _unique([*notes, *patch.notes]),
            "metadata": metadata,
        }
    )


def _remove_execution_node(
    nodes: dict[str, ExecutionNode],
    order: list[str],
    target_node_id: str,
) -> None:
    if target_node_id not in nodes:
        raise ValueError(f"unknown execution graph node: {target_node_id}")
    child_ids = [node.node_id for node in nodes.values() if node.parent_node_id == target_node_id]
    if child_ids:
        raise ValueError(
            f"cannot remove compound execution node with children: {target_node_id}:{','.join(child_ids)}"
        )
    nodes.pop(target_node_id)
    order.remove(target_node_id)
    for node_id, node in list(nodes.items()):
        if target_node_id not in node.depends_on:
            continue
        nodes[node_id] = node.model_copy(
            update={
                "depends_on": [
                    dependency_id
                    for dependency_id in node.depends_on
                    if dependency_id != target_node_id
                ]
            }
        )


def _rewire_execution_node(
    nodes: dict[str, ExecutionNode],
    target_node_id: str,
    *,
    new_depends_on: list[str],
    remove_depends_on: list[str],
) -> None:
    try:
        node = nodes[target_node_id]
    except KeyError as exc:
        raise ValueError(f"unknown execution graph node: {target_node_id}") from exc
    dependencies = [
        dependency_id
        for dependency_id in node.depends_on
        if dependency_id not in set(remove_depends_on)
    ]
    for dependency_id in new_depends_on:
        dependency_id = str(dependency_id or "").strip()
        if not dependency_id:
            continue
        if dependency_id not in nodes:
            raise ValueError(f"unknown execution graph dependency: {dependency_id}")
        if dependency_id == target_node_id:
            raise ValueError(f"execution graph node cannot depend on itself: {target_node_id}")
        if dependency_id not in dependencies:
            dependencies.append(dependency_id)
    nodes[target_node_id] = node.model_copy(update={"depends_on": dependencies})


def _replace_execution_subgraph(
    nodes: dict[str, ExecutionNode],
    order: list[str],
    target_node_id: str,
    replacement_nodes: list[ExecutionNode],
) -> None:
    if target_node_id not in nodes:
        raise ValueError(f"unknown execution graph node: {target_node_id}")
    target = nodes[target_node_id]
    removed_ids = {target_node_id}
    queue = deque([target_node_id])
    while queue:
        parent_id = queue.popleft()
        for node in list(nodes.values()):
            if node.parent_node_id == parent_id and node.node_id not in removed_ids:
                removed_ids.add(node.node_id)
                queue.append(node.node_id)

    replacement_ids = [node.node_id for node in replacement_nodes]
    if len(replacement_ids) != len(set(replacement_ids)):
        raise ValueError("duplicate replacement execution graph nodes")
    if set(replacement_ids) & (set(nodes) - removed_ids):
        raise ValueError("replacement execution graph node collides with existing node")

    external_downstream = [
        node_id
        for node_id, node in nodes.items()
        if node_id not in removed_ids and any(dep in removed_ids for dep in node.depends_on)
    ]
    target_index = min(order.index(node_id) for node_id in removed_ids)
    for node_id in removed_ids:
        nodes.pop(node_id, None)
        if node_id in order:
            order.remove(node_id)

    normalized_replacements: list[ExecutionNode] = []
    for node in replacement_nodes:
        parent_node_id = node.parent_node_id
        if parent_node_id in removed_ids or parent_node_id is None:
            parent_node_id = target.parent_node_id
        normalized = node.model_copy(update={"parent_node_id": parent_node_id})
        normalized_replacements.append(normalized)
        nodes[normalized.node_id] = normalized
    for offset, node in enumerate(normalized_replacements):
        order.insert(target_index + offset, node.node_id)

    replacement_sinks = _execution_sink_ids(normalized_replacements)
    for node_id in external_downstream:
        node = nodes[node_id]
        dependencies: list[str] = []
        for dependency_id in node.depends_on:
            if dependency_id in removed_ids:
                for sink_id in replacement_sinks:
                    if sink_id not in dependencies:
                        dependencies.append(sink_id)
            elif dependency_id not in dependencies:
                dependencies.append(dependency_id)
        nodes[node_id] = node.model_copy(update={"depends_on": dependencies})


def _reset_execution_subgraph(
    nodes: dict[str, ExecutionNode],
    target_node_id: str,
) -> None:
    if target_node_id not in nodes:
        raise ValueError(f"unknown execution graph node: {target_node_id}")
    reset_ids = {target_node_id}
    changed = True
    while changed:
        changed = False
        for node in nodes.values():
            if node.node_id in reset_ids:
                continue
            if node.parent_node_id in reset_ids or any(
                dependency_id in reset_ids for dependency_id in node.depends_on
            ):
                reset_ids.add(node.node_id)
                changed = True
    for node_id in reset_ids:
        _reset_execution_nodes(nodes, {node_id})


def _reset_execution_nodes(
    nodes: dict[str, ExecutionNode],
    reset_ids: set[str],
) -> None:
    for node_id in reset_ids:
        node = nodes.get(node_id)
        if node is None:
            continue
        nodes[node_id] = node.model_copy(
            update={
                "status": "pending",
                "attempt_count": 0,
                "input_artifact_ids": [],
                "output_artifact_ids": [],
            }
        )


def _dependency_descendants_from_nodes(
    nodes: dict[str, ExecutionNode],
    node_id: str,
) -> set[str]:
    downstream: dict[str, set[str]] = {}
    for node in nodes.values():
        for dependency_id in node.depends_on:
            downstream.setdefault(dependency_id, set()).add(node.node_id)
    result: set[str] = set()
    stack = list(downstream.get(node_id, set()))
    while stack:
        downstream_id = stack.pop()
        if downstream_id in result:
            continue
        result.add(downstream_id)
        stack.extend(downstream.get(downstream_id, set()))
    return result


def _descendants_from_nodes(
    nodes: dict[str, ExecutionNode],
    node_id: str,
) -> set[str]:
    children: dict[str, set[str]] = {}
    for node in nodes.values():
        if node.parent_node_id:
            children.setdefault(node.parent_node_id, set()).add(node.node_id)
    result: set[str] = set()
    stack = list(children.get(node_id, set()))
    while stack:
        child_id = stack.pop()
        if child_id in result:
            continue
        result.add(child_id)
        stack.extend(children.get(child_id, set()))
    return result


def _execution_sink_ids(nodes: list[ExecutionNode]) -> list[str]:
    node_ids = {node.node_id for node in nodes}
    upstream_ids = {
        dependency_id
        for node in nodes
        for dependency_id in node.depends_on
        if dependency_id in node_ids
    }
    sinks = [node.node_id for node in nodes if node.node_id not in upstream_ids]
    return sinks or [nodes[-1].node_id]


def affected_node_ids_for_patch(
    graph: ExecutionGraph,
    patch: ExecutionGraphPatch,
) -> set[str]:
    """Return old nodes whose outputs cannot remain current after a patch."""
    scheduler = ExecutionGraphScheduler()
    node_ids = {node.node_id for node in graph.nodes}
    affected: set[str] = set()
    for operation in patch.operations:
        target = str(operation.target_node_id or "").strip()
        if not target or target not in node_ids:
            continue
        if operation.op_type in {
            "remove_node",
            "rewire_dependency",
            "replace_subgraph",
            "rerun_from",
            "mark_terminal",
        }:
            affected.add(target)
            affected.update(scheduler.dependency_descendants(graph, target))
        if operation.op_type in {"remove_node", "replace_subgraph", "rerun_from"}:
            affected.update(scheduler.descendants(graph, target))
    return affected & node_ids


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


__all__ = [
    "ExecutionGraphScheduler",
    "ExecutionGraphValidationResult",
    "affected_node_ids_for_patch",
    "apply_execution_graph_patch",
]
