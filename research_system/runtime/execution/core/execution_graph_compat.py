from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from research_system.schemas.execution_graph import (
    ExecutionGraph,
    ExecutionGraphPatch,
    ExecutionGraphPatchOperation,
    ExecutionNode,
)
from research_system.schemas.planning import ActionPlan, ActionPlanPatch, ActionSpec
from research_system.schemas.subagents import (
    SubagentGraphPatch,
    SubagentPlan,
    SubagentPlanNode,
)


def action_plan_to_execution_graph(
    plan: ActionPlan,
    *,
    domain_id: str = "finance_ashare",
) -> ExecutionGraph:
    return ExecutionGraph(
        graph_id=plan.plan_id,
        domain_id=str(plan.dispatch_trace.get("domain_id") or domain_id),
        objective=str(plan.entry_reason or plan.intent),
        intent=plan.intent,
        strategy=plan.strategy,
        nodes=[action_spec_to_execution_node(action) for action in plan.actions],
        notes=list(plan.notes or []),
        metadata={
            "migration_source": "action_plan",
            "entry_strategy": plan.entry_strategy,
            "subagent_required": plan.subagent_required,
            "dispatch_trace": dict(plan.dispatch_trace or {}),
        },
    )


def action_frontier_to_execution_graph(
    pending_actions: list[ActionSpec],
    *,
    completed_node_ids: Iterable[str] = (),
    skipped_node_ids: Iterable[str] = (),
    failed_node_ids: Iterable[str] = (),
) -> ExecutionGraph:
    resolved_ids = list(dict.fromkeys([*completed_node_ids, *skipped_node_ids]))
    failed_ids = list(dict.fromkeys(failed_node_ids))
    pending_ids = {action.node_id for action in pending_actions}
    referenced_dependency_ids = {
        dependency_id
        for action in pending_actions
        for dependency_id in action.depends_on
    }
    implicitly_resolved_ids = sorted(
        referenced_dependency_ids - pending_ids - set(resolved_ids) - set(failed_ids)
    )
    resolved_ids.extend(implicitly_resolved_ids)
    placeholders = [
        ExecutionNode(
            node_id=node_id,
            node_kind="subagent",
            executor="runtime.completed",
            capability="runtime.completed",
            status="completed",
            parallel_safe=True,
            metadata={"migration_placeholder": True},
        )
        for node_id in resolved_ids
        if node_id not in pending_ids
    ]
    placeholders.extend(
        ExecutionNode(
            node_id=node_id,
            node_kind="subagent",
            executor="runtime.failed",
            capability="runtime.failed",
            status="failed",
            parallel_safe=True,
            metadata={"migration_placeholder": True},
        )
        for node_id in failed_ids
        if node_id not in pending_ids and node_id not in resolved_ids
    )
    return ExecutionGraph(
        domain_id="runtime",
        objective="Resolve legacy action frontier",
        strategy="dynamic",
        nodes=[*placeholders, *(action_spec_to_execution_node(action) for action in pending_actions)],
        metadata={"migration_source": "action_frontier"},
    )


def action_spec_to_execution_node(action: ActionSpec) -> ExecutionNode:
    return ExecutionNode(
        node_id=action.node_id,
        node_kind="subagent",
        executor=action.agent,
        capability=f"legacy.action.{action.agent}",
        objective=action.objective,
        inputs=dict(action.inputs or {}),
        depends_on=list(action.depends_on or []),
        status=action.status,
        tags=[*list(action.tags or []), "migration:legacy_action"],
        failure_mode=action.failure_mode,
        max_attempts=max(int(action.max_retries or 0), 0) + 1,
        replayable=action.replayable,
        cache_scope=action.cache_scope,
        cache_key=action.cache_key,
        parallel_safe=False,
        metadata={"action_id": action.action_id, "phase": action.phase},
    )


def action_plan_patch_to_execution_graph_patch(
    patch: ActionPlanPatch,
    *,
    base_revision: int,
) -> ExecutionGraphPatch:
    operations: list[ExecutionGraphPatchOperation] = []
    op_names = {
        "insert_action": "insert_node",
        "remove_action": "remove_node",
        "rewire_dependency": "rewire_dependency",
        "replace_subgraph": "replace_subgraph",
        "rerun_from": "rerun_from",
        "mark_terminal": "mark_terminal",
        "request_replan": "request_replan",
    }
    for operation in patch.operations:
        operations.append(
            ExecutionGraphPatchOperation(
                operation_id=operation.operation_id,
                op_type=op_names[operation.op_type],
                target_node_id=operation.target_node_id,
                node=(
                    action_spec_to_execution_node(operation.action)
                    if operation.action is not None
                    else None
                ),
                replacement_nodes=[
                    action_spec_to_execution_node(action)
                    for action in operation.replacement_actions
                ],
                new_depends_on=list(operation.new_depends_on),
                remove_depends_on=list(operation.remove_depends_on),
                reason=operation.reason,
                metadata=dict(operation.metadata),
            )
        )
    return ExecutionGraphPatch(
        patch_id=patch.patch_id,
        base_revision=base_revision,
        decision_type=patch.decision_type,
        operations=operations,
        notes=list(patch.notes),
        policy_source=patch.policy_source,
        confidence=patch.confidence,
        termination_reason=patch.termination_reason,
    )


def execution_graph_to_action_plan(graph: ExecutionGraph) -> ActionPlan:
    compound_node_ids = [node.node_id for node in graph.nodes if node.node_kind == "compound"]
    if compound_node_ids:
        raise ValueError(
            "legacy ActionPlan cannot execute compound nodes: " + ",".join(compound_node_ids)
        )
    strategy = graph.strategy if graph.strategy in {"static", "dynamic"} else "dynamic"
    metadata = dict(graph.metadata or {})
    return ActionPlan(
        plan_id=graph.graph_id,
        intent=str(graph.intent or metadata.get("intent") or "").strip(),
        strategy=strategy,
        actions=[_execution_node_to_action_spec(node) for node in graph.nodes],
        notes=list(graph.notes or []),
        entry_strategy=metadata.get("entry_strategy", "rule"),
        entry_reason=graph.objective,
        subagent_required=bool(metadata.get("subagent_required", True)),
        dispatch_trace={
            **dict(metadata.get("dispatch_trace") or {}),
            "execution_graph_revision": graph.revision,
        },
    )


def _execution_node_to_action_spec(node: ExecutionNode) -> ActionSpec:
    payload = {
        "node_id": node.node_id,
        "phase": str(node.metadata.get("phase") or node.capability or "execute"),
        "agent": str(node.executor or ""),
        "objective": node.objective,
        "inputs": dict(node.inputs or {}),
        "status": node.status if node.status in {"pending", "completed", "skipped"} else "pending",
        "tags": [tag for tag in node.tags if tag != "migration:legacy_action"],
        "depends_on": list(node.depends_on or []),
        "max_retries": max(int(node.max_attempts or 1) - 1, 0),
        "failure_mode": node.failure_mode,
        "cache_scope": node.cache_scope,
        "cache_key": node.cache_key,
        "replayable": node.replayable,
    }
    action_id = str(node.metadata.get("action_id") or "").strip()
    if action_id:
        payload["action_id"] = action_id
    return ActionSpec.model_validate(payload)


def subagent_plan_to_execution_graph(
    plan: SubagentPlan,
    *,
    domain_id: str = "finance_ashare",
) -> ExecutionGraph:
    return ExecutionGraph(
        graph_id=plan.plan_id,
        domain_id=domain_id,
        objective=str(plan.intent or plan.phase or "Execute subagent plan"),
        intent=plan.intent,
        strategy=plan.strategy,
        nodes=[subagent_plan_node_to_execution_node(node) for node in plan.nodes],
        notes=list(plan.notes or []),
        metadata={
            "migration_source": "subagent_plan",
            "parent_action_id": plan.parent_action_id,
            "parent_node_id": plan.parent_node_id,
            "phase": plan.phase,
            "catalog_trace": dict(plan.catalog_trace or {}),
        },
    )


def subagent_plan_node_to_execution_node(node: SubagentPlanNode) -> ExecutionNode:
    return ExecutionNode(
        node_id=node.node_id,
        node_kind="subagent",
        executor=node.subagent_type,
        capability=node.subagent_type,
        objective=node.objective,
        inputs=dict(node.inputs or {}),
        depends_on=list(node.depends_on or []),
        tags=[*list(node.tags or []), "migration:subagent_plan"],
        failure_mode="isolate" if node.failure_mode == "isolate" else "halt",
        allow_partial_dependencies=node.allow_partial_dependencies,
        max_attempts=node.max_attempts,
        replayable=node.replayable,
        cache_scope=node.cache_scope,
        cache_key=node.cache_key,
        parallel_safe=True,
        metadata={"notes": list(node.notes or [])},
    )


def subagent_graph_patch_to_execution_graph_patch(
    patch: SubagentGraphPatch,
    *,
    base_revision: int,
) -> ExecutionGraphPatch:
    op_names = {
        "insert_subagent": "insert_node",
        "remove_node": "remove_node",
        "rewire_dependency": "rewire_dependency",
        "replace_subgraph": "replace_subgraph",
        "rerun_from": "rerun_from",
        "mark_terminal": "mark_terminal",
        "request_replan": "request_replan",
    }

    def convert(node: SubagentPlanNode) -> ExecutionNode:
        return subagent_plan_node_to_execution_node(node)

    return ExecutionGraphPatch(
        patch_id=patch.patch_id,
        base_revision=base_revision,
        decision_type=patch.decision_type,
        operations=[
            ExecutionGraphPatchOperation(
                operation_id=operation.operation_id,
                op_type=op_names[operation.op_type],
                target_node_id=operation.target_node_id,
                node=convert(operation.node) if operation.node is not None else None,
                replacement_nodes=[convert(node) for node in operation.replacement_nodes],
                new_depends_on=list(operation.new_depends_on),
                remove_depends_on=list(operation.remove_depends_on),
                reason=operation.reason,
                metadata=dict(operation.metadata),
            )
            for operation in patch.operations
        ],
        notes=list(patch.notes),
        policy_source=patch.policy_source,
        confidence=patch.confidence,
        termination_reason=patch.termination_reason,
    )


def execution_graph_to_subagent_plan(graph: ExecutionGraph) -> SubagentPlan:
    if any(node.node_kind == "compound" for node in graph.nodes):
        raise ValueError("legacy SubagentPlan cannot execute compound nodes")
    metadata = dict(graph.metadata or {})
    strategy = graph.strategy if graph.strategy in {"catalog", "rule", "llm", "hybrid"} else "catalog"
    return SubagentPlan(
        plan_id=graph.graph_id,
        intent=graph.intent,
        phase=str(metadata.get("phase") or "") or None,
        parent_action_id=metadata.get("parent_action_id"),
        parent_node_id=metadata.get("parent_node_id"),
        strategy=strategy,
        nodes=[
            SubagentPlanNode(
                node_id=node.node_id,
                subagent_type=str(node.executor),
                objective=node.objective,
                inputs=dict(node.inputs),
                tags=[tag for tag in node.tags if tag != "migration:subagent_plan"],
                depends_on=list(node.depends_on),
                failure_mode="isolate" if node.failure_mode == "isolate" else "raise",
                allow_partial_dependencies=node.allow_partial_dependencies,
                max_attempts=node.max_attempts,
                replayable=node.replayable,
                cache_scope=node.cache_scope,
                cache_key=node.cache_key,
                notes=list(node.metadata.get("notes") or []),
            )
            for node in graph.nodes
        ],
        notes=list(graph.notes),
        catalog_trace={
            **dict(metadata.get("catalog_trace") or {}),
            "execution_graph_revision": graph.revision,
        },
    )


def subagent_specs_to_execution_graph(
    specs: Iterable[Any],
    *,
    graph_id: str,
    domain_id: str = "finance_ashare",
) -> ExecutionGraph:
    nodes = []
    for spec in specs:
        metadata = dict(getattr(spec, "metadata", {}) or {})
        nodes.append(
            ExecutionNode(
                node_id=str(spec.node_id),
                node_kind="subagent",
                executor=str(metadata.get("subagent_type") or metadata.get("agent") or spec.node_id),
                capability=str(metadata.get("capability") or metadata.get("subagent_type") or spec.node_id),
                depends_on=list(getattr(spec, "depends_on", ()) or ()),
                tags=["migration:subagent_dag_spec"],
                failure_mode="isolate" if getattr(spec, "failure_mode", "raise") == "isolate" else "halt",
                allow_partial_dependencies=bool(getattr(spec, "allow_partial_dependencies", False)),
                max_attempts=max(int(getattr(spec, "max_attempts", 1) or 1), 1),
                replayable=bool(getattr(spec, "replayable", True)),
                cache_scope=getattr(spec, "cache_scope", None),
                cache_key=getattr(spec, "cache_key", None),
                parallel_safe=True,
                metadata=metadata,
            )
        )
    return ExecutionGraph(
        graph_id=graph_id,
        domain_id=domain_id,
        objective="Execute subagent DAG",
        strategy="dynamic",
        nodes=nodes,
        metadata={"migration_source": "subagent_dag_specs"},
    )


__all__ = [
    "action_frontier_to_execution_graph",
    "action_plan_to_execution_graph",
    "action_plan_patch_to_execution_graph_patch",
    "action_spec_to_execution_node",
    "execution_graph_to_action_plan",
    "execution_graph_to_subagent_plan",
    "subagent_graph_patch_to_execution_graph_patch",
    "subagent_plan_to_execution_graph",
    "subagent_plan_node_to_execution_node",
    "subagent_specs_to_execution_graph",
]
