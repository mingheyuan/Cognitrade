from __future__ import annotations

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.schemas.domain_pack import (
    CapabilityDefinition,
    CapabilityPlanningRequest,
)
from research_system.schemas.execution_graph import (
    ExecutionGraph,
    ExecutionGraphPatch,
    ExecutionGraphPatchOperation,
    ExecutionNode,
)


class CapabilityPlanningError(RuntimeError):
    pass


class CapabilityPlanner:
    def __init__(self, registry: DomainPackRegistry) -> None:
        self.registry = registry

    def build_lazy_graph(self, request: CapabilityPlanningRequest) -> ExecutionGraph:
        """Create the root compound and defer leaf selection to a safe point."""
        requested_version = str(request.metadata.get("domain_version") or "").strip() or None
        manifest = self.registry.get(request.domain_id, version=requested_version)
        required_outputs = self._required_artifact_types(manifest, request)
        metadata = dict(request.metadata or {})
        metadata.update(
            {
                "domain_version": manifest.version,
                "available_artifact_types": sorted(set(request.available_artifact_types)),
                "required_artifact_types": required_outputs,
                "planner": "capability_contract_lazy",
                "planning_request": request.model_dump(mode="json"),
            }
        )
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
            intent=str(metadata.get("intent") or "") or None,
            strategy="dynamic",
            governance=governance,
            nodes=[
                ExecutionNode(
                    node_id="domain_task",
                    node_kind="compound",
                    capability=f"{manifest.domain_id}.task",
                    objective=request.objective,
                    expansion_policy="lazy",
                    metadata={
                        "domain_id": manifest.domain_id,
                        "domain_version": manifest.version,
                        "planning_request": request.model_dump(mode="json"),
                    },
                )
            ],
            metadata=metadata,
        )

    def build_graph(self, request: CapabilityPlanningRequest) -> ExecutionGraph:
        requested_version = str(request.metadata.get("domain_version") or "").strip() or None
        manifest = self.registry.get(request.domain_id, version=requested_version)
        intent = str(request.metadata.get("intent") or "").strip()
        intent_definition = next(
            (item for item in manifest.intake.intents if item.intent_id == intent),
            None,
        )
        intent_capabilities = manifest.planner_policies.get("intent_capabilities") or {}
        policy_preferred = list(
            (intent_definition.candidate_capabilities if intent_definition else None)
            or intent_capabilities.get(intent)
            or []
        )
        if policy_preferred:
            request = request.model_copy(
                update={
                    "preferred_capabilities": list(
                        dict.fromkeys([*request.preferred_capabilities, *policy_preferred])
                    )
                }
            )
        available = set(request.available_artifact_types)
        required_outputs = self._required_artifact_types(manifest, request)
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
                    failure_mode=capability.failure_mode,
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

    @staticmethod
    def _required_artifact_types(
        manifest,
        request: CapabilityPlanningRequest,
    ) -> list[str]:
        required_outputs = list(dict.fromkeys(request.required_artifact_types))
        intent = str(request.metadata.get("intent") or "").strip()
        intent_definition = next(
            (item for item in manifest.intake.intents if item.intent_id == intent),
            None,
        )
        intent_outputs = manifest.planner_policies.get("intent_outputs") or {}
        for artifact_type in list(
            (intent_definition.output_artifact_types if intent_definition else None)
            or intent_outputs.get(intent)
            or []
        ):
            if artifact_type not in required_outputs:
                required_outputs.append(str(artifact_type))
        return required_outputs

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
        excluded_subagent_types = {
            str(item).strip()
            for item in request.metadata.get("exclude_subagent_types") or []
            if str(item).strip()
        }
        fallback_priority = [
            str(item).strip()
            for item in request.metadata.get("fallback_priority") or []
            if str(item).strip()
        ]
        fallback_rank = {
            item: index for index, item in enumerate(fallback_priority)
        }
        intent = str(request.metadata.get("intent") or "").strip()
        candidates = []
        cost_rank = {"low": 0, "medium": 1, "high": 2}
        latency_rank = {"low": 0, "medium": 1, "high": 2}
        for capability in self.registry.capabilities(
            domain_id=request.domain_id,
            produces=artifact_type,
            version=(str(request.metadata.get("domain_version") or "").strip() or None),
        ):
            if capability.subagent_type in excluded_subagent_types:
                continue
            intent_tags = {
                tag.split(":", 1)[1]
                for tag in capability.tags
                if tag.startswith("intent:") and ":" in tag
            }
            if intent_tags and intent not in intent_tags:
                continue
            if capability.side_effect_level not in allowed_side_effects:
                continue
            if allowed_tools and not set(capability.tools).issubset(allowed_tools):
                continue
            if (
                capability.permissions
                and allowed_permissions
                and not set(capability.permissions).issubset(allowed_permissions)
            ):
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
                fallback_rank.get(item.capability, fallback_rank.get(item.subagent_type, len(fallback_rank))),
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


class CapabilityDynamicPlanner:
    """Materialize the next graph frontier from the current Board state."""

    def __init__(self, planner: CapabilityPlanner) -> None:
        self.planner = planner

    def expand_compound(
        self,
        *,
        graph: ExecutionGraph,
        compound: ExecutionNode,
        artifacts,
    ) -> ExecutionGraphPatch:
        payload = compound.metadata.get("planning_request") or graph.metadata.get(
            "planning_request"
        )
        if not isinstance(payload, dict):
            raise CapabilityPlanningError(
                f"lazy compound has no planning request: {compound.node_id}"
            )
        request = CapabilityPlanningRequest.model_validate(payload)
        available = {
            *request.available_artifact_types,
            *(artifact.artifact_type for artifact in artifacts),
        }
        request = request.model_copy(
            update={
                "available_artifact_types": sorted(available),
                "metadata": {
                    **dict(request.metadata or {}),
                    "domain_version": graph.metadata.get("domain_version"),
                },
            }
        )
        materialized = self.planner.build_graph(request)
        existing_ids = {node.node_id for node in graph.nodes}
        operations: list[ExecutionGraphPatchOperation] = []
        for node in materialized.nodes:
            if node.node_kind != "subagent" or node.node_id in existing_ids:
                continue
            operations.append(
                ExecutionGraphPatchOperation(
                    op_type="insert_node",
                    node=node.model_copy(
                        update={"parent_node_id": compound.node_id},
                        deep=True,
                    ),
                    reason="capability planner materialized the next frontier",
                )
            )
        if not operations:
            raise CapabilityPlanningError(
                f"lazy compound produced no executable capabilities: {compound.node_id}"
            )
        return ExecutionGraphPatch(
            base_revision=graph.revision,
            decision_type="continue",
            policy_source="hybrid",
            operations=operations,
            notes=[
                "Materialized from current task, installed capability catalog, and Board artifacts.",
            ],
        )

    def evaluate_frontier(
        self,
        *,
        graph: ExecutionGraph,
        frontier: list[ExecutionNode],
        artifacts,
    ) -> ExecutionGraphPatch | None:
        """Ask the capability catalog for a replacement when a required output is absent."""
        payload = graph.metadata.get("planning_request")
        if not isinstance(payload, dict):
            return None
        request = CapabilityPlanningRequest.model_validate(payload)
        request = request.model_copy(
            update={
                "metadata": {
                    **dict(request.metadata or {}),
                    "domain_version": graph.metadata.get("domain_version"),
                }
            }
        )
        control_requests = [
            row
            for row in list(graph.metadata.get("control_requests") or [])
            if isinstance(row, dict)
        ]
        requested_artifacts = [
            str(artifact_type).strip()
            for row in control_requests
            for artifact_type in list(row.get("requested_artifact_types") or [])
            if str(artifact_type).strip()
        ]
        preferred_capabilities = [
            str(capability).strip()
            for row in control_requests
            for capability in list(row.get("preferred_capabilities") or [])
            if str(capability).strip()
        ]
        retry_targets = [
            str(row.get("target_node_id") or "").strip()
            for row in control_requests
            if row.get("kind") == "request_retry" and str(row.get("target_node_id") or "").strip()
        ]
        if retry_targets:
            return ExecutionGraphPatch(
                base_revision=graph.revision,
                decision_type="continue",
                policy_source="hybrid",
                operations=[
                    ExecutionGraphPatchOperation(
                        op_type="rerun_from",
                        target_node_id=node_id,
                        reason="Subagent requested a governed retry at the next safe point.",
                    )
                    for node_id in dict.fromkeys(retry_targets)
                ],
                notes=["Applied Subagent retry request at a safe point."],
            )
        if requested_artifacts or preferred_capabilities:
            request = request.model_copy(
                update={
                    "required_artifact_types": list(
                        dict.fromkeys(
                            [*request.required_artifact_types, *requested_artifacts]
                        )
                    ),
                    "preferred_capabilities": list(
                        dict.fromkeys(
                            [*request.preferred_capabilities, *preferred_capabilities]
                        )
                    ),
                }
            )
        active_types = {
            artifact.artifact_type
            for artifact in artifacts
            if artifact.status == "active"
        }
        required = self.planner._required_artifact_types(
            self.planner.registry.get(
                request.domain_id,
                version=(str(request.metadata.get("domain_version") or "").strip() or None),
            ),
            request,
        )
        missing = [artifact_type for artifact_type in required if artifact_type not in active_types]
        if not missing:
            return None
        failed_subagents = [
            node.executor
            for node in graph.nodes
            if node.node_kind == "subagent" and node.status == "failed" and node.executor
        ]
        if failed_subagents:
            fallback_priority: list[str] = []
            for failed_subagent in failed_subagents:
                failed_definition = next(
                    (
                        item
                        for item in self.planner.registry.capabilities(
                            domain_id=request.domain_id,
                            version=(str(request.metadata.get("domain_version") or "").strip() or None),
                        )
                        if item.subagent_type == failed_subagent
                    ),
                    None,
                )
                if failed_definition is None:
                    continue
                fallback_priority.extend(failed_definition.fallback_capabilities)
                fallback_priority.extend(failed_definition.fallback_subagent_types)
            request = request.model_copy(
                update={
                    "metadata": {
                        **dict(request.metadata or {}),
                        "exclude_subagent_types": failed_subagents,
                        "fallback_priority": list(dict.fromkeys(fallback_priority)),
                    }
                }
            )
        request = request.model_copy(
            update={
                "available_artifact_types": sorted(active_types),
                "required_artifact_types": missing,
            }
        )
        try:
            candidate_graph = self.planner.build_graph(request)
        except CapabilityPlanningError:
            return None
        existing_ids = {node.node_id for node in graph.nodes}
        operations: list[ExecutionGraphPatchOperation] = []
        parent_id = next(
            (node.node_id for node in graph.nodes if node.node_kind == "compound"),
            None,
        )
        for node in candidate_graph.nodes:
            if node.node_kind != "subagent" or node.node_id in existing_ids:
                continue
            operations.append(
                ExecutionGraphPatchOperation(
                    op_type="insert_node",
                    node=node.model_copy(update={"parent_node_id": parent_id}, deep=True),
                    reason=f"required artifacts missing after frontier: {','.join(missing)}",
                )
            )
        if not operations:
            return None
        return ExecutionGraphPatch(
            base_revision=graph.revision,
            decision_type="replan",
            policy_source="hybrid",
            operations=operations,
            notes=["Planner evaluated the Board at a safe point and added a replacement frontier."],
        )


__all__ = [
    "CapabilityDynamicPlanner",
    "CapabilityPlanner",
    "CapabilityPlanningError",
]
