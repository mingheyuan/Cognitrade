from __future__ import annotations

from collections.abc import Callable
from typing import Any

from research_system.adapters.vendors.http_json_source_registry import HttpJsonSourceRegistry
from research_system.adapters.vendors.mcp_source_registry import McpSourceRegistry
from research_system.adapters.vendors.openapi_source_registry import OpenApiSourceRegistry
from research_system.adapters.vendors.skill_source_registry import SkillSourceRegistry
from research_system.agents._subagent_capability_gap_controller import (
    SubagentCapabilityGapController,
)
from research_system.agents._subagent_control_escalation import (
    SubagentControlEscalationDecision,
    SubagentControlEscalationPolicyService,
)
from research_system.agents._subagent_catalog_controller import (
    SubagentCatalogController,
)
from research_system.agents._subagent_control_controller import (
    SubagentControlController,
)
from research_system.agents._subagent_discovery_controller import SubagentDiscoveryController
from research_system.agents._subagent_discovery_decision_source import SubagentDiscoveryDecisionSource
from research_system.agents._subagent_discovery_policy import SubagentDiscoveryPolicyService
from research_system.agents._subagent_discovery_policy_controller import (
    SubagentDiscoveryPolicyController,
)
from research_system.agents._subagent_discovery_policy_source import SubagentDiscoveryLLMPolicySource
from research_system.agents._subagent_dag import (
    SubagentDagExecutor,
    SubagentDagNodeResult,
    SubagentDagNodeSpec,
)
from research_system.agents._subagent_phase_execution_controller import (
    SubagentPhaseExecutionController,
)
from research_system.agents._subagent_phase_policy_source import SubagentPhaseLLMPolicySource
from research_system.agents._subagent_phase_shadow_policy import SubagentPhaseLLMShadowPolicy
from research_system.agents._subagent_discovery_shadow_policy import SubagentDiscoveryLLMShadowPolicy
from research_system.runtime.orchestration._subagent_discovery_candidate_policy import (
    SubagentDiscoveryCandidatePolicyService,
)
from research_system.runtime.orchestration._subagent_discovery_approval_policy import (
    SubagentDiscoveryApprovalPolicyService,
)
from research_system.runtime.orchestration._subagent_discovery_activation_policy import (
    SubagentDiscoveryActivationPolicyService,
)
from research_system.runtime.orchestration._runtime_control_stage_recorder import (
    RuntimeControlStageRecorder,
)
from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    SubagentCapabilityGapEscalationControlStageDecision,
)
from research_system.runtime.orchestration._subagent_control_stage_recorder import (
    SubagentControlStageRecorder,
)
from research_system.runtime.orchestration.subagent_discovery_candidate_sources import (
    SubagentDiscoveryCandidateSupport,
)
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentCapabilityGap,
    SubagentGraphPatch,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
    SubagentToolDiscoveryRequest,
)
from research_system.subagents.planning import apply_subagent_graph_patch
from research_system.subagents.registry import SubagentRegistry


class SubagentCapabilityGapError(KeyError):
    def __init__(
        self,
        *,
        gap: SubagentCapabilityGap,
        tool_discovery_request: SubagentToolDiscoveryRequest | None = None,
    ) -> None:
        self.gap = gap
        self.tool_discovery_request = tool_discovery_request
        super().__init__(gap.reason)


class SubagentPhaseController:
    orchestrator_error_cls = RuntimeError

    def __init__(
        self,
        *,
        registry: SubagentRegistry,
        invoke_subagent: Callable[[SubagentInvocation], tuple[dict[str, Any], Any]],
        emit_event: Callable[..., None] | None,
        cache_context_builder: Callable[..., Any],
        start_from_node_id_builder: Callable[..., str | None],
        runtime_binding_getter: Callable[[], dict[str, Any]] | None = None,
        llm_provider=None,
        settings=None,
        service_name: str | None = None,
    ) -> None:
        self.registry = registry
        self.invoke_subagent = invoke_subagent
        self.emit_event = emit_event
        self.cache_context_builder = cache_context_builder
        self.start_from_node_id_builder = start_from_node_id_builder
        self.runtime_binding_getter = runtime_binding_getter
        self.settings = settings
        self.llm_provider = llm_provider
        self.control_stage_recorder = RuntimeControlStageRecorder(
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
        )
        self.subagent_control_stage_recorder = SubagentControlStageRecorder(
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
            runtime_control_stage_recorder=self.control_stage_recorder,
        )
        self.shadow_policy = SubagentPhaseLLMShadowPolicy(
            llm_provider=llm_provider,
            settings=settings,
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
            service_name=service_name,
        )
        self.llm_policy_source = SubagentPhaseLLMPolicySource(
            shadow_policy=self.shadow_policy,
            settings=settings,
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
        )
        self.discovery_policy = SubagentDiscoveryPolicyService(settings=settings)
        self.discovery_shadow_policy = SubagentDiscoveryLLMShadowPolicy(
            llm_provider=llm_provider,
            settings=settings,
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
            service_name=service_name,
        )
        self.control_escalation_policy = SubagentControlEscalationPolicyService(settings=settings)
        self.discovery_llm_policy_source = SubagentDiscoveryLLMPolicySource(
            shadow_policy=self.discovery_shadow_policy,
            settings=settings,
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
        )
        self.discovery_candidate_support = SubagentDiscoveryCandidateSupport(self)
        self.discovery_candidate_policy = SubagentDiscoveryCandidatePolicyService(
            llm_provider=llm_provider,
            settings=settings,
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
            service_name=service_name,
        )
        self.discovery_approval_policy = SubagentDiscoveryApprovalPolicyService(settings=settings)
        self.discovery_activation_policy = SubagentDiscoveryActivationPolicyService(settings=settings)
        self.discovery_decision_source = SubagentDiscoveryDecisionSource(
            discovery_policy=self.discovery_policy,
            discovery_llm_policy_source=self.discovery_llm_policy_source,
            discovery_candidate_policy=self.discovery_candidate_policy,
            discovery_approval_policy=self.discovery_approval_policy,
            discovery_activation_policy=self.discovery_activation_policy,
            discovery_candidate_support=self.discovery_candidate_support,
        )
        self.discovery_stage_policy_source = self.discovery_decision_source.stage_policy_source
        self.discovery_policy_controller = SubagentDiscoveryPolicyController(
            decision_source=self.discovery_decision_source,
            stage_policy_source=self.discovery_stage_policy_source,
            runtime_packaging=self.discovery_candidate_support.runtime_packaging,
            error_factory=self.orchestrator_error_cls,
        )
        self.discovery_controller = SubagentDiscoveryController(
            discovery_candidate_support=self.discovery_candidate_support,
            discovery_decision_source=self.discovery_decision_source,
            activation_host=self,
            discovery_policy_controller=self.discovery_policy_controller,
            control_stage_recorder=self.subagent_control_stage_recorder,
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
            service_name=service_name,
        )
        self.capability_gap_controller = SubagentCapabilityGapController(
            capability_gap_builder=self._build_capability_gap,
            discovery_controller=self.discovery_controller,
            candidates_resolver=self._catalog_candidates,
            entry_selector=self._choose_catalog_entry,
            escalation_policy=self.control_escalation_policy,
            discovery_store_resolver=lambda: self._binding_value("subagent_discovery_store"),
            runtime_binding_getter=runtime_binding_getter,
            record_capability_gap=self._record_capability_gap,
            record_tool_discovery_request=self._record_tool_discovery_request,
            record_tool_discovery_auto_activation=self._record_tool_discovery_auto_activation,
            record_escalation_decision=self._record_capability_gap_escalation,
            error_factory=SubagentCapabilityGapError,
        )
        self.catalog_controller = SubagentCatalogController(
            candidates_resolver=self._catalog_candidates,
            entry_selector=self._choose_catalog_entry,
            capability_gap_controller=self.capability_gap_controller,
            control_stage_recorder=self.subagent_control_stage_recorder,
            service_name=service_name,
        )
        self.phase_execution_controller = SubagentPhaseExecutionController(
            execute_plan=self.execute_plan,
            apply_patch=self.apply_patch,
            llm_policy_source=self.llm_policy_source,
            patch_requests_replan=self._patch_requests_replan,
            resolve_variant_id=self._resolve_variant_id,
            control_stage_recorder=self.subagent_control_stage_recorder,
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
            service_name=service_name,
        )
        self.control_controller = SubagentControlController(
            catalog_controller=self.catalog_controller,
            phase_execution_controller=self.phase_execution_controller,
        )
        self._mcp_source_registry = None
        self._http_json_source_registry = None
        self._openapi_source_registry = None
        self._skill_source_registry = None
        self._skill_runtime = None

    def select_catalog_entry(
        self,
        *,
        stage: str,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ) -> SubagentCatalogEntry:
        return self.control_controller.select_catalog_entry(
            stage=stage,
            intent=intent,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )

    def build_catalog_plan_node(
        self,
        *,
        node_id: str,
        subagent_type: str,
        objective: str,
        inputs: dict[str, Any],
        depends_on: list[str] | None = None,
        tags: list[str] | None = None,
        failure_mode: str = "raise",
        allow_partial_dependencies: bool = False,
        max_attempts: int = 1,
        replayable: bool = True,
        cache_scope: str | None = "run",
        cache_key: str | None = None,
        notes: list[str] | None = None,
    ) -> SubagentPlanNode:
        return SubagentPlanNode(
            node_id=node_id,
            subagent_type=subagent_type,
            objective=objective,
            inputs=dict(inputs),
            depends_on=list(depends_on or []),
            tags=list(tags or ["catalog_selected"]),
            failure_mode=failure_mode,  # type: ignore[arg-type]
            allow_partial_dependencies=allow_partial_dependencies,
            max_attempts=max_attempts,
            replayable=replayable,
            cache_scope=cache_scope,  # type: ignore[arg-type]
            cache_key=cache_key,
            notes=list(notes or []),
        )

    def execute_plan(
        self,
        *,
        plan: SubagentPlan,
        build_invocation: dict[str, dict[str, Any]],
        dag_id: str,
        max_workers: int | None = None,
    ) -> dict[str, SubagentDagNodeResult]:
        dag_nodes: list[SubagentDagNodeSpec] = []
        for plan_node in plan.nodes:
            runtime_node = build_invocation[plan_node.node_id]
            dag_nodes.append(
                SubagentDagNodeSpec(
                    node_id=plan_node.node_id,
                    build_invocation=lambda results, *, builder=runtime_node["builder"]: builder(results),
                    depends_on=tuple(plan_node.depends_on),
                    failure_mode=plan_node.failure_mode,
                    allow_partial_dependencies=plan_node.allow_partial_dependencies,
                    max_attempts=plan_node.max_attempts,
                    replayable=plan_node.replayable,
                    cache_scope=plan_node.cache_scope,
                    cache_key=plan_node.cache_key,
                    metadata={
                        **dict(runtime_node["metadata"]),
                        "plan_id": plan.plan_id,
                        "plan_node_id": plan_node.node_id,
                        "subagent_plan_strategy": plan.strategy,
                    },
                )
            )
        executor = SubagentDagExecutor(
            invoke_subagent=self.invoke_subagent,
            emit_event=self.emit_event,
        )
        resolved_start_from_node_id = self.start_from_node_id_builder(dag_id=dag_id)
        return executor.execute(
            dag_nodes,
            max_workers=max_workers,
            cache_context=self.cache_context_builder(dag_id=dag_id),
            start_from_node_id=resolved_start_from_node_id,
        )

    def apply_patch(
        self,
        *,
        plan: SubagentPlan,
        patch: SubagentGraphPatch | None,
    ) -> tuple[SubagentPlan, list[dict[str, Any]]]:
        if patch is None:
            return plan, []
        updated_plan = apply_subagent_graph_patch(plan, patch)
        return updated_plan, [patch.model_dump(mode="json")]

    def execute_patch_cycle(
        self,
        *,
        plan: SubagentPlan,
        dag_id: str,
        base_max_workers: int | None,
        final_max_workers: int | None,
        build_invocation_factory: Callable[[SubagentPlan], dict[str, dict[str, Any]]],
        patch_builder: Callable[[dict[str, SubagentDagNodeResult]], SubagentGraphPatch | None],
        replan_builder: Callable[[SubagentPlan, SubagentGraphPatch, dict[str, SubagentDagNodeResult]], SubagentPlan] | None = None,
        allow_current_plan_variant: bool = False,
        candidate_plan_variants_builder: Callable[
            [SubagentPlan, SubagentGraphPatch | None, dict[str, SubagentDagNodeResult]],
            dict[str, SubagentPlan],
        ]
        | None = None,
    ) -> tuple[SubagentPlan, dict[str, SubagentDagNodeResult], list[dict[str, Any]], dict[str, SubagentDagNodeResult]]:
        resolution = self.control_controller.execute_patch_cycle(
            plan=plan,
            dag_id=dag_id,
            base_max_workers=base_max_workers,
            final_max_workers=final_max_workers,
            build_invocation_factory=build_invocation_factory,
            patch_builder=patch_builder,
            replan_builder=replan_builder,
            allow_current_plan_variant=allow_current_plan_variant,
            candidate_plan_variants_builder=candidate_plan_variants_builder,
        )
        return (
            resolution.final_plan,
            resolution.base_results,
            resolution.final_patch_rows,
            resolution.final_results,
        )

    @staticmethod
    def _patch_requests_replan(patch: SubagentGraphPatch) -> bool:
        if patch.decision_type == "replan":
            return True
        return any(operation.op_type == "request_replan" for operation in patch.operations)

    @staticmethod
    def _resolve_variant_id(
        *,
        target_plan: SubagentPlan,
        candidate_plans: dict[str, SubagentPlan],
        fallback: str,
    ) -> str:
        matched_fallback = fallback
        for variant_id, candidate_plan in candidate_plans.items():
            if candidate_plan is target_plan:
                if variant_id != fallback:
                    return variant_id
                matched_fallback = variant_id
        return matched_fallback

    def build_metadata(
        self,
        *,
        task_name: str,
        agent_path: str,
        objective: str | None,
        phase: str,
        extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_name": task_name,
            "agent_path": agent_path,
            "objective": objective,
            "phase": phase,
        }
        payload.update(dict(extras or {}))
        return payload

    def build_invocation(
        self,
        *,
        subagent_type: str,
        objective: str | None,
        inputs: dict[str, Any],
        task_name: str,
        agent_path: str,
        phase: str,
        extra_context: dict[str, Any] | None = None,
    ) -> SubagentInvocation:
        context = {
            "task_name": task_name,
            "agent_path": agent_path,
            "phase": phase,
        }
        context.update(dict(extra_context or {}))
        return SubagentInvocation(
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            context=context,
        )

    def trace_from_results(
        self,
        *,
        results: dict[str, SubagentDagNodeResult],
    ) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = []
        for node_id, result in results.items():
            metadata = dict(result.metadata or {})
            record = {
                "node_id": node_id,
                "subagent_type": str(
                    (result.record.subagent_type if result.record is not None else metadata.get("subagent_type"))
                    or node_id
                ),
                "status": result.status,
                "objective": str(
                    (result.record.objective if result.record is not None else metadata.get("objective")) or ""
                ),
                "invocation_id": result.invocation_id,
                "task_name": metadata.get("task_name"),
                "agent_path": metadata.get("agent_path"),
                "cache_hit": bool(result.cache_hit),
                "attempts": result.attempts,
            }
            if metadata.get("plan_node_id"):
                record["plan_node_id"] = metadata["plan_node_id"]
            if metadata.get("subagent_plan_strategy"):
                record["subagent_plan_strategy"] = metadata["subagent_plan_strategy"]
            child_thread_id = str(metadata.get("child_thread_id") or "").strip()
            child_turn_id = str(metadata.get("child_turn_id") or "").strip()
            if child_thread_id:
                record["child_thread_id"] = child_thread_id
            if child_turn_id:
                record["child_turn_id"] = child_turn_id
            if result.record is not None and result.record.degrade_reason:
                record["degrade_reason"] = result.record.degrade_reason
            if result.blocked_by:
                record["blocked_by"] = list(result.blocked_by)
            if result.retry_errors:
                record["retry_errors"] = list(result.retry_errors)
            trace.append(record)
        return trace

    @staticmethod
    def _cost_rank(cost_class: str | None) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(str(cost_class or "").strip().lower(), 3)

    @staticmethod
    def _latency_rank(latency_class: str | None) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(str(latency_class or "").strip().lower(), 3)


    def _build_capability_gap(
        self,
        *,
        stage: str,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
    ) -> SubagentCapabilityGap:
        stage_entries = self.registry.catalog_entries(stage=stage, include_candidates=True)
        candidate_entries = self.registry.catalog_entries(
            stage=stage,
            capability=capability,
            include_candidates=True,
        )
        notes = [
            "catalog_capability_gap",
            f"stage={stage}",
            f"intent={intent}",
            f"capability={capability}",
        ]
        if any("approved_discovery_candidate" in list(entry.tags or []) for entry in candidate_entries):
            notes.append("approved_discovery_candidates_available")
        return SubagentCapabilityGap(
            intent=intent,
            stage=stage,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
            reason=f"no catalog entry for stage={stage!r} intent={intent!r} capability={capability!r}",
            candidate_subagent_types=[
                str(entry.subagent_type)
                for entry in candidate_entries
                if str(entry.subagent_type or "").strip()
            ],
            available_stage_capabilities=sorted(
                {
                    str(item).strip()
                    for entry in stage_entries
                    for item in list(entry.capabilities or [])
                    if str(item).strip()
                }
            ),
            available_intents=sorted(
                {
                    str(item).strip()
                    for entry in stage_entries
                    for item in list(entry.suitable_intents or [])
                    if str(item).strip()
                }
            ),
            notes=notes,
        )

    def _record_capability_gap(self, *, gap: SubagentCapabilityGap) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or gap.intent or "").strip()
        if context is None or support is None or not request_id or not intent:
            return
        payload = {"event": "subagent_capability_gap_detected", **gap.model_dump(mode="json")}
        context.subagent_capability_gaps.append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type="subagent_capability_gap_detected",
                message=f"Detected capability gap for {gap.capability} in stage {gap.stage}",
                payload=dict(payload),
                phase=str(gap.stage or "").strip() or None,
                agent=None,
            )
        else:
            support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="subagent_capability_gap_detected",
                phase=str(gap.stage or "").strip() or None,
                agent=None,
                message=f"Detected capability gap for {gap.capability} in stage {gap.stage}",
                payload=dict(payload),
            )

    def _record_tool_discovery_request(self, *, request: SubagentToolDiscoveryRequest) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or request.intent or "").strip()
        if context is None or support is None or not request_id or not intent:
            return
        payload = {"event": "subagent_tool_discovery_requested", **request.model_dump(mode="json")}
        context.subagent_tool_discovery_requests.append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type="subagent_tool_discovery_requested",
                message=f"Requested tool discovery for capability {request.capability}",
                payload=dict(payload),
                phase=str(request.stage or "").strip() or None,
                agent=None,
            )
        else:
            support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="subagent_tool_discovery_requested",
                phase=str(request.stage or "").strip() or None,
                agent=None,
                message=f"Requested tool discovery for capability {request.capability}",
                payload=dict(payload),
            )

    def _record_capability_gap_escalation(
        self,
        *,
        gap: SubagentCapabilityGap,
        escalation_decision: SubagentControlEscalationDecision,
    ) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        phase = str(binding.get("phase") or gap.stage or "").strip() or None
        agent = str(binding.get("agent") or "subagent_control").strip() or "subagent_control"
        if context is None or support is None or not request_id:
            return
        stage_decision = SubagentCapabilityGapEscalationControlStageDecision(
            intent=gap.intent,
            stage=gap.stage,
            capability=gap.capability,
            preferred_subagent_type=gap.preferred_subagent_type,
            requires_llm=gap.requires_llm,
            decision_type="request_action_replan"
            if escalation_decision.strategy == "action_replan"
            else "continue_discovery",
            escalation_strategy=escalation_decision.strategy,
            policy_source=escalation_decision.policy_source,
            rule_id=escalation_decision.rule_id,
            reason=escalation_decision.reason,
            notes=list(escalation_decision.notes or ()),
        )
        self.subagent_control_stage_recorder.record(
            event_type=stage_decision.event_type,
            message=stage_decision.message(),
            payload=stage_decision.event_payload(),
            phase=phase,
            agent=agent,
        )

    def require_mcp_source_registry(self):
        if isinstance(self._mcp_source_registry, McpSourceRegistry):
            return self._mcp_source_registry
        self._mcp_source_registry = self._binding_value("mcp_source_registry")
        if isinstance(self._mcp_source_registry, McpSourceRegistry):
            return self._mcp_source_registry
        self._mcp_source_registry = McpSourceRegistry(settings=self.settings)
        return self._mcp_source_registry

    def require_http_json_source_registry(self):
        if isinstance(self._http_json_source_registry, HttpJsonSourceRegistry):
            return self._http_json_source_registry
        self._http_json_source_registry = self._binding_value("http_json_source_registry")
        if isinstance(self._http_json_source_registry, HttpJsonSourceRegistry):
            return self._http_json_source_registry
        self._http_json_source_registry = HttpJsonSourceRegistry(settings=self.settings)
        return self._http_json_source_registry

    def require_openapi_source_registry(self):
        if isinstance(self._openapi_source_registry, OpenApiSourceRegistry):
            return self._openapi_source_registry
        self._openapi_source_registry = self._binding_value("openapi_source_registry")
        if isinstance(self._openapi_source_registry, OpenApiSourceRegistry):
            return self._openapi_source_registry
        self._openapi_source_registry = OpenApiSourceRegistry(settings=self.settings)
        return self._openapi_source_registry

    def require_skill_source_registry(self):
        if isinstance(self._skill_source_registry, SkillSourceRegistry):
            return self._skill_source_registry
        self._skill_source_registry = self._binding_value("skill_source_registry")
        if isinstance(self._skill_source_registry, SkillSourceRegistry):
            return self._skill_source_registry
        self._skill_source_registry = SkillSourceRegistry(settings=self.settings)
        return self._skill_source_registry

    def require_skill_runtime(self):
        runtime = self._skill_runtime
        if runtime is not None:
            return runtime
        runtime = self._binding_value("skill_runtime")
        if runtime is not None:
            self._skill_runtime = runtime
            return runtime
        runtime = SkillRuntime(self.settings) if self.settings is not None else SkillRuntime()
        self._skill_runtime = runtime
        return runtime

    def require_subagent_discovery_store(self):
        store = self._binding_value("subagent_discovery_store")
        if store is None:
            raise self.orchestrator_error_cls("subagent discovery store is not available")
        return store

    def _catalog_candidates(
        self,
        *,
        stage: str,
        intent: str,
        capability: str,
        requires_llm: bool | None,
    ) -> list[SubagentCatalogEntry]:
        candidates = self.registry.catalog_entries(
            stage=stage,
            intent=intent,
            capability=capability,
            requires_llm=requires_llm,
        )
        if candidates:
            return candidates
        return self.registry.catalog_entries(
            stage=stage,
            capability=capability,
            requires_llm=requires_llm,
        )

    def _choose_catalog_entry(
        self,
        candidates: list[SubagentCatalogEntry],
        *,
        preferred_subagent_type: str | None,
    ) -> SubagentCatalogEntry:
        if preferred_subagent_type:
            for candidate in candidates:
                if candidate.subagent_type == preferred_subagent_type:
                    return candidate
        return sorted(
            candidates,
            key=lambda candidate: (
                bool(candidate.requires_llm),
                self._cost_rank(candidate.cost_class),
                self._latency_rank(candidate.latency_class),
                candidate.subagent_type,
            ),
        )[0]

    def _record_tool_discovery_auto_activation(self, *, ticket) -> None:
        if ticket is None:
            return
        activation_entry = self._system_auto_activation_entry(ticket)
        if activation_entry is None:
            return
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(
            binding.get("intent")
            or getattr(getattr(ticket, "capability_gap", None), "intent", None)
            or ""
        ).strip()
        if context is None or support is None or not request_id or not intent:
            return
        runtime_registration = getattr(activation_entry, "runtime_registration", None)
        payload = {
            "event": "subagent_tool_discovery_auto_activated",
            "ticket_id": ticket.ticket_id,
            "stage": ticket.capability_gap.stage,
            "intent": ticket.capability_gap.intent,
            "capability": ticket.capability_gap.capability,
            "candidate_subagent_type": activation_entry.subagent_type,
            "registration_type": (
                runtime_registration.registration_type if runtime_registration is not None else None
            ),
            "registration_template_id": (
                runtime_registration.registration_template_id if runtime_registration is not None else None
            ),
            "activated_by": activation_entry.activated_by,
            "activation_reason": activation_entry.activation_reason,
        }
        context.dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type="subagent_tool_discovery_auto_activated",
                message=f"Auto-activated discovery alias {activation_entry.subagent_type}",
                payload=dict(payload),
                phase=str(ticket.capability_gap.stage or "").strip() or None,
                agent=None,
            )
            return
        support.emit_event(
            context,
            request_id=request_id,
            intent=intent,
            event_type="subagent_tool_discovery_auto_activated",
            phase=str(ticket.capability_gap.stage or "").strip() or None,
            agent=None,
            message=f"Auto-activated discovery alias {activation_entry.subagent_type}",
            payload=dict(payload),
        )

    @staticmethod
    def _system_auto_activation_entry(ticket):
        for entry in list(getattr(ticket, "activated_entries", []) or []):
            if str(getattr(entry, "activated_by", "") or "").strip() == "system_auto_activate":
                return entry
        return None

    def _binding_value(self, key: str):
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        value = binding.get(key)
        if value is not None:
            return value
        factory_name = f"require_{key}"
        factory = binding.get(factory_name)
        if callable(factory):
            try:
                return factory()
            except Exception:
                return None
        return None


__all__ = ["SubagentCapabilityGapError", "SubagentPhaseController"]
