from __future__ import annotations

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.schemas.domain_pack import (
    CapabilityDefinition,
    CapabilityPlanningRequest,
)
from research_system.schemas.execution_graph import ExecutionGraph, ExecutionNode


class CapabilityPlanningError(RuntimeError):
    pass


class CapabilityPlanner:
    def __init__(self, registry: DomainPackRegistry) -> None:
        self.registry = registry

    def build_graph(self, request: CapabilityPlanningRequest) -> ExecutionGraph:
        manifest = self.registry.get(request.domain_id)
        available = set(request.available_artifact_types)
        required_outputs = list(dict.fromkeys(request.required_artifact_types))
        intent = str(request.metadata.get("intent") or "").strip()
        intent_outputs = manifest.planner_policies.get("intent_outputs") or {}
        for artifact_type in list(intent_outputs.get(intent) or []):
            if artifact_type not in required_outputs:
                required_outputs.append(str(artifact_type))
        selected_by_type: dict[str, str] = {}
        selected_by_subagent: dict[str, ExecutionNode] = {}
        resolving: set[str] = set()

        def resolve_artifact(artifact_type: str) -> str | None:
            if artifact_type in available:
                return None
            if artifact_type in selected_by_type:
                return selected_by_type[artifact_type]
            if artifact_type in resolving:
                raise CapabilityPlanningError(f"capability artifact cycle: {artifact_type}")
            resolving.add(artifact_type)
            try:
                candidates = self._candidate_producers(
                    request=request,
                    artifact_type=artifact_type,
                )
                if not candidates:
                    raise CapabilityPlanningError(
                        f"no capability can produce required artifact: {artifact_type}"
                    )
                capability = candidates[0]
                existing = selected_by_subagent.get(capability.subagent_type)
                if existing is not None:
                    for produced in capability.produces:
                        selected_by_type.setdefault(produced.artifact_type, existing.node_id)
                    return existing.node_id

                dependencies: list[str] = []
                for consumed in capability.consumes:
                    if not consumed.required:
                        continue
                    producer_node_id = resolve_artifact(consumed.artifact_type)
                    if producer_node_id and producer_node_id not in dependencies:
                        dependencies.append(producer_node_id)
                node_id = self._node_id(capability.subagent_type, selected_by_subagent)
                node = ExecutionNode(
                    node_id=node_id,
                    node_kind="subagent",
                    executor=capability.subagent_type,
                    capability=capability.capability,
                    parent_node_id="domain_task",
                    objective=capability.description or capability.capability,
                    inputs=dict(request.metadata.get("intent_inputs") or {}),
                    consumes=[port.model_copy(deep=True) for port in capability.consumes],
                    produces=[port.model_copy(deep=True) for port in capability.produces],
                    depends_on=dependencies,
                    failure_mode="halt",
                    max_attempts=manifest.governance.max_retries_per_node + 1,
                    replayable=capability.replayable,
                    cache_scope=capability.cache_scope,
                    parallel_safe=capability.parallel_safe,
                    max_concurrency=capability.max_concurrency,
                    tools=list(capability.tools),
                    permissions=list(capability.permissions),
                    estimated_tokens=capability.estimated_tokens,
                    estimated_cost=capability.estimated_cost,
                    side_effect_level=capability.side_effect_level,
                    metadata={
                        "domain_id": manifest.domain_id,
                        "domain_version": manifest.version,
                        "tools": list(capability.tools),
                        "permissions": list(capability.permissions),
                        "cost_class": capability.cost_class,
                        "latency_class": capability.latency_class,
                        "reliability": capability.reliability,
                        "side_effect_level": capability.side_effect_level,
                        "requires_llm": capability.requires_llm,
                        "implementation": capability.implementation,
                    },
                )
                selected_by_subagent[capability.subagent_type] = node
                for produced in capability.produces:
                    selected_by_type[produced.artifact_type] = node_id
                return node_id
            finally:
                resolving.remove(artifact_type)

        for artifact_type in required_outputs:
            resolve_artifact(artifact_type)

        nodes = [
            ExecutionNode(
                node_id="domain_task",
                node_kind="compound",
                capability=f"{manifest.domain_id}.task",
                objective=request.objective,
                expansion_policy="eager",
                metadata={
                    "domain_id": manifest.domain_id,
                    "domain_version": manifest.version,
                },
            ),
            *selected_by_subagent.values(),
        ]
        required_gate_ids = list(
            dict.fromkeys(
                [
                    gate.gate_id
                    for gate in manifest.quality_gates
                    if gate.blocking and gate.artifact_type in required_outputs
                ]
            )
        )
        governance = manifest.governance.model_copy(
            update={
                "required_gate_ids": required_gate_ids,
                "token_budget": request.token_budget or manifest.governance.token_budget,
                "cost_budget": (
                    request.cost_budget
                    if request.cost_budget is not None
                    else manifest.governance.cost_budget
                ),
            }
        )
        return ExecutionGraph(
            domain_id=manifest.domain_id,
            objective=request.objective,
            strategy="dynamic",
            nodes=nodes,
            governance=governance,
            metadata={
                "domain_version": manifest.version,
                "available_artifact_types": sorted(available),
                "required_artifact_types": required_outputs,
                "planner": "capability_contract",
                **dict(request.metadata or {}),
            },
        )

    def _candidate_producers(
        self,
        *,
        request: CapabilityPlanningRequest,
        artifact_type: str,
    ) -> list[CapabilityDefinition]:
        allowed_tools = set(request.allowed_tools)
        allowed_permissions = set(request.allowed_permissions)
        allowed_side_effects = set(request.allowed_side_effect_levels)
        preferred = set(request.preferred_capabilities)
        candidates = []
        cost_rank = {"low": 0, "medium": 1, "high": 2}
        latency_rank = {"low": 0, "medium": 1, "high": 2}
        for capability in self.registry.capabilities(
            domain_id=request.domain_id,
            produces=artifact_type,
        ):
            if capability.side_effect_level not in allowed_side_effects:
                continue
            if allowed_tools and not set(capability.tools).issubset(allowed_tools):
                continue
            if capability.permissions and not set(capability.permissions).issubset(allowed_permissions):
                continue
            if cost_rank[capability.cost_class] > cost_rank[request.max_cost_class]:
                continue
            if latency_rank[capability.latency_class] > latency_rank[request.max_latency_class]:
                continue
            if capability.reliability < request.minimum_reliability:
                continue
            candidates.append(capability)
        candidates.sort(
            key=lambda item: (
                0 if item.capability in preferred else 1,
                cost_rank[item.cost_class],
                latency_rank[item.latency_class],
                -item.reliability,
                item.subagent_type,
            )
        )
        return candidates

    @staticmethod
    def _node_id(subagent_type: str, selected: dict[str, ExecutionNode]) -> str:
        base = subagent_type.replace(".", "_").replace("-", "_")
        node_id = base
        suffix = 2
        existing_ids = {node.node_id for node in selected.values()}
        while node_id in existing_ids:
            node_id = f"{base}_{suffix}"
            suffix += 1
        return node_id


__all__ = ["CapabilityPlanner", "CapabilityPlanningError"]
