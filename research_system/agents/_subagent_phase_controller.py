from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from research_system.adapters.vendors.http_json_source_registry import HttpJsonSourceRegistry
from research_system.adapters.vendors.mcp_source_registry import McpSourceRegistry
from research_system.adapters.vendors.openapi_source_registry import OpenApiSourceRegistry
from research_system.adapters.vendors.skill_source_registry import SkillSourceRegistry
from research_system.agents._subagent_discovery_policy import SubagentDiscoveryPolicyService
from research_system.agents._subagent_discovery_policy_source import SubagentDiscoveryLLMPolicySource
from research_system.agents._subagent_dag import (
    SubagentDagExecutor,
    SubagentDagNodeResult,
    SubagentDagNodeSpec,
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
from research_system.runtime.orchestration.subagent_discovery_candidate_sources import (
    SubagentDiscoveryCandidateSupport,
)
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentCapabilityGap,
    SubagentDiscoveryReviewEvent,
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
        self.discovery_llm_policy_source = SubagentDiscoveryLLMPolicySource(
            shadow_policy=self.discovery_shadow_policy,
            settings=settings,
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
        )
        self.discovery_candidate_support = SubagentDiscoveryCandidateSupport(self)
        self.discovery_candidate_policy = SubagentDiscoveryCandidatePolicyService(
            llm_provider=None,
            settings=settings,
        )
        self.discovery_approval_policy = SubagentDiscoveryApprovalPolicyService(settings=settings)
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
        candidates = self.registry.catalog_entries(
            stage=stage,
            intent=intent,
            capability=capability,
            requires_llm=requires_llm,
        )
        if not candidates:
            candidates = self.registry.catalog_entries(
                stage=stage,
                capability=capability,
                requires_llm=requires_llm,
            )
        if not candidates:
            gap = self._build_capability_gap(
                stage=stage,
                intent=intent,
                capability=capability,
                preferred_subagent_type=preferred_subagent_type,
                requires_llm=requires_llm,
            )
            discovery_request = self._build_tool_discovery_request(gap=gap)
            self._persist_tool_discovery_request(
                gap=gap,
                request=discovery_request,
            )
            self._record_capability_gap(gap=gap)
            self._record_tool_discovery_request(request=discovery_request)
            raise SubagentCapabilityGapError(
                gap=gap,
                tool_discovery_request=discovery_request,
            )
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
        base_results = self.execute_plan(
            plan=plan,
            build_invocation=build_invocation_factory(plan),
            dag_id=dag_id,
            max_workers=base_max_workers,
        )
        patch = patch_builder(base_results)
        rule_variant_id = "current"
        rule_final_plan = plan
        rule_patch_rows: list[dict[str, Any]] = []
        if patch is not None and self._patch_requests_replan(patch):
            if replan_builder is None:
                raise ValueError("subagent patch requested replan but no replan_builder was provided")
            rule_variant_id = "rule_replanned"
            rule_final_plan = replan_builder(plan, patch, base_results)
            rule_patch_rows = [patch.model_dump(mode="json")]
        else:
            rule_final_plan, rule_patch_rows = self.apply_patch(plan=plan, patch=patch)
            if patch is not None and rule_patch_rows:
                rule_variant_id = "rule_patched"
        candidate_plans: dict[str, SubagentPlan] = {}
        if allow_current_plan_variant:
            candidate_plans["current"] = plan
        if patch is not None and rule_variant_id != "current":
            candidate_plans[rule_variant_id] = rule_final_plan
        if candidate_plan_variants_builder is not None:
            for variant_id, variant_plan in dict(
                candidate_plan_variants_builder(rule_final_plan, patch, base_results) or {}
            ).items():
                if isinstance(variant_plan, SubagentPlan):
                    candidate_plans[str(variant_id)] = variant_plan
        rule_variant_id = self._resolve_variant_id(
            target_plan=rule_final_plan,
            candidate_plans=candidate_plans,
            fallback=rule_variant_id,
        )
        final_plan, final_patch, final_patch_rows, final_plan_variant, shadow_payload = self.llm_policy_source.apply(
            dag_id=dag_id,
            plan=plan,
            candidate_plans=candidate_plans,
            base_results=base_results,
            rule_final_plan=rule_final_plan,
            rule_patch=patch,
            rule_patch_rows=rule_patch_rows,
            rule_variant_id=rule_variant_id,
        )
        final_results = base_results if not final_patch_rows and final_plan is plan else self.execute_plan(
            plan=final_plan,
            build_invocation=build_invocation_factory(final_plan),
            dag_id=dag_id,
            max_workers=final_max_workers,
        )
        self.llm_policy_source.observe(
            dag_id=dag_id,
            plan=plan,
            candidate_plans=candidate_plans,
            actual_final_plan=final_plan,
            actual_patch=final_patch,
            actual_plan_variant=final_plan_variant,
            base_results=base_results,
            shadow_payload=shadow_payload,
        )
        return final_plan, base_results, final_patch_rows, final_results

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

    def _build_tool_discovery_request(
        self,
        *,
        gap: SubagentCapabilityGap,
    ) -> SubagentToolDiscoveryRequest:
        search_terms = [
            str(gap.intent or "").strip(),
            str(gap.stage or "").strip(),
            str(gap.capability or "").strip(),
            str(gap.preferred_subagent_type or "").strip(),
        ]
        preferred_protocols, policy_notes = self.discovery_policy.resolve(
            intent=gap.intent,
            stage=gap.stage,
            capability=gap.capability,
            preferred_subagent_type=gap.preferred_subagent_type,
            requires_llm=gap.requires_llm,
        )
        preferred_protocols, policy_notes, _ = self.discovery_llm_policy_source.apply(
            gap=gap,
            candidate_protocols=list(self.discovery_policy.default_preferred_protocols),
            rule_preferred_protocols=preferred_protocols,
            rule_notes=policy_notes,
        )
        return SubagentToolDiscoveryRequest(
            intent=gap.intent,
            stage=gap.stage,
            capability=gap.capability,
            search_query=" ".join(token for token in search_terms if token),
            preferred_protocols=preferred_protocols,
            reason=gap.reason,
            policy_source=gap.policy_source,
            notes=[
                "tool_discovery_requested_from_capability_gap",
                f"gap_id={gap.gap_id}",
                *policy_notes,
            ],
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

    def _persist_tool_discovery_request(
        self,
        *,
        gap: SubagentCapabilityGap,
        request: SubagentToolDiscoveryRequest,
    ) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        store = binding.get("subagent_discovery_store")
        create_request = getattr(store, "create_request", None)
        if not callable(create_request):
            return
        try:
            ticket = create_request(
                gap=gap,
                request=request,
            )
            self._preview_discovery_ticket(ticket=ticket, store=store)
        except Exception:
            return

    def _preview_discovery_ticket(self, *, ticket, store) -> None:
        try:
            candidate_entries = self.discovery_candidate_support.candidate_entries(ticket)
        except Exception:
            return
        if not candidate_entries:
            return
        ticket.candidate_catalog_entries = list(candidate_entries)
        recommendation, shadow_recommendation = self.discovery_candidate_policy.recommend(
            ticket=ticket,
            candidate_entries=ticket.candidate_catalog_entries,
        )
        ticket.candidate_recommendation = recommendation
        ticket.candidate_shadow_recommendation = shadow_recommendation
        preview_notes = list(ticket.notes or [])
        if "candidate_preview_generated_from_runtime_discovery_request" not in preview_notes:
            preview_notes.append("candidate_preview_generated_from_runtime_discovery_request")
        if recommendation is not None:
            recommendation_note = f"recommended_candidate={recommendation.candidate_subagent_type}"
            if recommendation_note not in preview_notes:
                preview_notes.append(recommendation_note)
        ticket.notes = preview_notes
        approval_decision = self.discovery_approval_policy.evaluate(ticket=ticket)
        self._apply_discovery_review_decision(
            ticket=ticket,
            decision=str(approval_decision.get("decision") or "pending"),
            reviewer=str(approval_decision.get("reviewer") or "system_auto_gate"),
            reason=str(approval_decision.get("reason") or "pending_manual_review_discovery_preview"),
            source=str(approval_decision.get("source") or "auto_discovery_policy"),
            extra_notes=[
                str(item).strip()
                for item in list(approval_decision.get("notes") or [])
                if str(item).strip()
            ],
        )
        save = getattr(store, "save", None)
        if callable(save):
            try:
                save(ticket, previous_status="pending")
            except Exception:
                return

    @staticmethod
    def _apply_discovery_review_decision(
        *,
        ticket,
        decision: str,
        reviewer: str,
        reason: str,
        source: str,
        extra_notes: list[str] | None = None,
    ) -> None:
        previous_status = str(ticket.status or "pending").strip() or "pending"
        reviewed_at = datetime.now(timezone.utc)
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision == "approved":
            ticket.status = "approved"
        elif normalized_decision == "rejected":
            ticket.status = "rejected"
        else:
            ticket.status = "pending"
        ticket.reviewer = reviewer
        ticket.review_reason = reason
        ticket.reviewed_at = reviewed_at
        ticket.updated_at = reviewed_at
        review_history = list(ticket.review_history or [])
        review_history.append(
            SubagentDiscoveryReviewEvent(
                decision=ticket.status,  # type: ignore[arg-type]
                reviewer=reviewer,
                reason=reason,
                reviewed_at=reviewed_at,
                previous_status=previous_status,  # type: ignore[arg-type]
                source=source,
            )
        )
        ticket.review_history = review_history
        notes = list(ticket.notes or [])
        for note in list(extra_notes or []):
            if note and note not in notes:
                notes.append(note)
        ticket.notes = notes

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
