from __future__ import annotations

from pathlib import Path

from research_system.runtime.execution.core.action_runner import OrchestratorActionRunner
from research_system.runtime.orchestration.orchestrator_admin import OrchestratorAdminService
from research_system.runtime.orchestration.orchestrator_assembly_support import (
    OrchestratorAssemblySupport,
)
from research_system.runtime.orchestration.orchestrator_compat_support import (
    OrchestratorCompatSupport,
)
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.runtime.orchestration.orchestrator_dependencies import (
    OrchestratorDependencies,
    build_orchestrator_dependencies,
)
from research_system.runtime.execution.core.plan_executor import OrchestratorPlanExecutor
from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.orchestration.orchestrator_run_coordinator import OrchestratorRunCoordinator
from research_system.runtime.orchestration.orchestrator_research_forwarding_support import (
    OrchestratorResearchForwardingSupport,
)
from research_system.runtime.orchestration.orchestrator_research_support import OrchestratorResearchSupport
from research_system.runtime.orchestration.orchestrator_runtime_io_support import (
    OrchestratorRuntimeIoSupport,
)
from research_system.runtime.orchestration.orchestrator_subagent_bridge import OrchestratorSubagentBridge
from research_system.runtime.platform.settings import Settings
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorError(Exception):
    pass


class RunCancelledError(OrchestratorError):
    pass


class RunTimedOutError(OrchestratorError):
    pass


class ResearchOrchestrator:
    orchestrator_error_cls = OrchestratorError
    run_cancelled_error_cls = RunCancelledError
    run_timed_out_error_cls = RunTimedOutError

    def __init__(
        self,
        settings: Settings,
        event_listener=None,
        dependencies: OrchestratorDependencies | None = None,
    ) -> None:
        self.settings = settings
        self.event_listener = event_listener
        self.dependencies = dependencies or build_orchestrator_dependencies(settings)
        self._bind_dependencies(self.dependencies)
        self.assembly = OrchestratorAssemblySupport(self)
        self.research_support = self._build_research_support()
        self.execution_support = self._build_execution_support()
        self.admin = self._build_admin_service()
        self.action_runner = self._build_action_runner()
        self.plan_executor = self._build_plan_executor()
        self.run_coordinator = self._build_run_coordinator()
        self.subagent_bridge = self._build_subagent_bridge()
        self.runtime_io = self._build_runtime_io_support()
        self.research_forwarding = self._build_research_forwarding_support()
        self.compat = self._build_compat_support()
        self._attach_subagent_observers()

    def _bind_dependencies(self, dependencies: OrchestratorDependencies) -> None:
        self.run_store = dependencies.run_store
        self.provider_registry = dependencies.provider_registry
        self.llm_provider = dependencies.llm_provider
        self.task_router = dependencies.task_router
        self.progress_service = dependencies.progress_service
        self.skill_runtime = dependencies.skill_runtime
        self.collector = dependencies.collector
        self.distiller = dependencies.distiller
        self.critic = dependencies.critic
        self.reviewer = dependencies.reviewer
        self.retriever = dependencies.retriever
        self.reporter = dependencies.reporter
        self.screener = dependencies.screener
        self.screen_reviewer = dependencies.screen_reviewer
        self.comps_service = dependencies.comps_service
        self.dcf_service = dependencies.dcf_service
        self.cashflow_resilience_service = dependencies.cashflow_resilience_service
        self.proposal_store = dependencies.proposal_store
        self.subagent_discovery_store = dependencies.subagent_discovery_store
        self.methodology_evolver = dependencies.methodology_evolver
        self.proposal_generator = dependencies.proposal_generator
        self.methodology_manifest_service = dependencies.methodology_manifest_service
        self.retrieval_policy_service = dependencies.retrieval_policy_service
        self.memory_service = dependencies.memory_service
        self.methodology_memory_writer = dependencies.methodology_memory_writer
        self.gates = dependencies.gates
        self.retention = dependencies.retention
        self.planner = dependencies.planner
        self.supervisor = dependencies.supervisor

    def _build_admin_service(self) -> OrchestratorAdminService:
        return self._assembly().build_admin_service()

    def _assembly(self) -> OrchestratorAssemblySupport:
        assembly = getattr(self, "assembly", None)
        if assembly is None:
            assembly = OrchestratorAssemblySupport(self)
            self.assembly = assembly
        return assembly

    def _admin(self) -> OrchestratorAdminService:
        return self._assembly().admin()

    def _build_research_support(self) -> OrchestratorResearchSupport:
        return self._assembly().build_research_support()

    def _research_support(self) -> OrchestratorResearchSupport:
        return self._assembly().research_support()

    def _build_execution_support(self) -> OrchestratorExecutionSupport:
        return self._assembly().build_execution_support()

    def _execution_support(self) -> OrchestratorExecutionSupport:
        return self._assembly().execution_support()

    def _build_action_runner(self) -> OrchestratorActionRunner:
        return self._assembly().build_action_runner()

    def _action_runner(self) -> OrchestratorActionRunner:
        return self._assembly().action_runner()

    def _build_plan_executor(self) -> OrchestratorPlanExecutor:
        return self._assembly().build_plan_executor()

    def _plan_executor(self) -> OrchestratorPlanExecutor:
        return self._assembly().plan_executor()

    def _build_subagent_bridge(self) -> OrchestratorSubagentBridge:
        return self._assembly().build_subagent_bridge()

    def _subagent_bridge(self) -> OrchestratorSubagentBridge:
        return self._assembly().subagent_bridge()

    def _build_run_coordinator(self) -> OrchestratorRunCoordinator:
        return self._assembly().build_run_coordinator()

    def _run_coordinator(self) -> OrchestratorRunCoordinator:
        return self._assembly().run_coordinator()

    def _build_runtime_io_support(self) -> OrchestratorRuntimeIoSupport:
        return self._assembly().build_runtime_io_support()

    def _runtime_io(self) -> OrchestratorRuntimeIoSupport:
        return self._assembly().runtime_io()

    def _build_research_forwarding_support(self) -> OrchestratorResearchForwardingSupport:
        return self._assembly().build_research_forwarding_support()

    def _research_forwarding(self) -> OrchestratorResearchForwardingSupport:
        return self._assembly().research_forwarding()

    def _build_compat_support(self) -> OrchestratorCompatSupport:
        return self._assembly().build_compat_support()

    def _compat(self) -> OrchestratorCompatSupport:
        return self._assembly().compat()

    def close(self) -> None:
        close = getattr(self.dependencies, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "ResearchOrchestrator":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _attach_subagent_observers(self) -> None:
        self._subagent_bridge().attach_observers()

    def run(self, request: RequestEnvelope) -> ResponseEnvelope:
        return self._run_coordinator().run(request)

    def replay(self, run_id: str) -> dict:
        return self._admin().replay(run_id)

    def trace(self, run_id: str) -> dict:
        return self._admin().trace(run_id)

    def events(self, run_id: str) -> dict:
        return self._admin().events(run_id)

    def metrics(self, run_id: str) -> dict:
        return self._admin().metrics(run_id)

    def thread(self, run_id: str, thread_id: str) -> dict:
        return self._admin().thread(run_id, thread_id)

    def thread_replay(self, run_id: str, thread_id: str) -> dict:
        return self._admin().thread_replay(run_id, thread_id)

    def thread_metrics(self, run_id: str, thread_id: str) -> dict:
        return self._admin().thread_metrics(run_id, thread_id)

    def subagent(self, run_id: str, invocation_id: str) -> dict:
        return self._admin().subagent(run_id, invocation_id)

    def steps(self, run_id: str) -> dict:
        return self._admin().steps(run_id)

    def history(self) -> list[dict]:
        return self._admin().history()

    def list_proposals(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        return self._admin().list_proposals(status=status, limit=limit)

    def list_subagent_discovery_requests(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        return self._admin().list_subagent_discovery_requests(status=status, limit=limit)

    def show_subagent_discovery_request(self, ticket_id: str) -> dict:
        return self._admin().show_subagent_discovery_request(ticket_id)

    def review_subagent_discovery_request(
        self,
        ticket_id: str,
        *,
        decision: str,
        reviewer: str = "manual_cli_review",
        reason: str | None = None,
        candidate_catalog_entries: list[dict] | None = None,
    ) -> dict:
        return self._admin().review_subagent_discovery_request(
            ticket_id,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            candidate_catalog_entries=candidate_catalog_entries,
        )

    def activate_subagent_discovery_request(
        self,
        ticket_id: str,
        *,
        delegate_subagent_type: str | None = None,
        candidate_subagent_type: str | None = None,
        registration_template_id: str | None = None,
        activated_by: str = "manual_cli_activation",
        reason: str | None = None,
        registration_type: str | None = None,
        skill_name: str | None = None,
        snapshot_type: str | None = None,
        source_id: str | None = None,
        tool_name: str | None = None,
        endpoint_path: str | None = None,
        http_method: str | None = None,
        openapi_spec_uri: str | None = None,
        openapi_operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        return self._admin().activate_subagent_discovery_request(
            ticket_id,
            delegate_subagent_type=delegate_subagent_type,
            candidate_subagent_type=candidate_subagent_type,
            registration_template_id=registration_template_id,
            activated_by=activated_by,
            reason=reason,
            registration_type=registration_type,
            skill_name=skill_name,
            snapshot_type=snapshot_type,
            source_id=source_id,
            tool_name=tool_name,
            endpoint_path=endpoint_path,
            http_method=http_method,
            openapi_spec_uri=openapi_spec_uri,
            openapi_operation_id=openapi_operation_id,
            timeout_seconds=timeout_seconds,
        )

    def show_proposal(self, proposal_id: str) -> dict:
        return self._admin().show_proposal(proposal_id)

    def review_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        reviewer: str = "manual_cli_review",
        reason: str | None = None,
    ) -> dict:
        return self._admin().review_proposal(
            proposal_id,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
        )

    def resume(self, run_id: str) -> dict:
        return self._admin().resume(run_id)

    def rerun_from(self, run_id: str, *, from_node: str) -> ResponseEnvelope:
        return self._run_coordinator().rerun_from(run_id, from_node=from_node)

    def rerun_subagent_from(
        self,
        run_id: str,
        *,
        action_node: str,
        dag_id: str,
        from_node: str,
    ) -> ResponseEnvelope:
        return self._run_coordinator().rerun_subagent_from(
            run_id,
            action_node=action_node,
            dag_id=dag_id,
            from_node=from_node,
        )

    def cancel(self, run_id: str) -> dict:
        return self._admin().cancel(run_id)

    def cleanup(self) -> dict:
        return self._admin().cleanup()

    def rebuild_qdrant(self) -> dict:
        return self._admin().rebuild_qdrant()

    def _execute_plan(self, *, context: RuntimeContext, request: RequestEnvelope, plan: ActionPlan) -> None:
        self._compat().execute_plan(context=context, request=request, plan=plan)

    def _run_action(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        intent: str,
        action: ActionSpec,
        completed_node_ids: list[str],
        pending_actions_after_current: list[ActionSpec],
    ) -> None:
        self._compat().run_action(
            context=context,
            request=request,
            plan=plan,
            intent=intent,
            action=action,
            completed_node_ids=completed_node_ids,
            pending_actions_after_current=pending_actions_after_current,
        )

    def _add_entry(self, context: RuntimeContext, request: RequestEnvelope, intent: str, entry: BlackboardEntry) -> None:
        self._compat().add_entry(context, request, intent, entry)

    def _emit_event(
        self,
        context: RuntimeContext,
        *,
        request_id: str,
        intent: str,
        event_type: str,
        message: str,
        phase: str | None = None,
        agent: str | None = None,
        payload: dict | None = None,
    ) -> None:
        self._compat().emit_event(
            context,
            request_id=request_id,
            intent=intent,
            event_type=event_type,
            message=message,
            phase=phase,
            agent=agent,
            payload=payload,
        )

    def _transition(
        self,
        context: RuntimeContext,
        status: str,
        *,
        request_id: str,
        intent: str,
        message: str,
        error_summary: str | None = None,
    ) -> None:
        self._compat().transition(
            context,
            status,
            request_id=request_id,
            intent=intent,
            message=message,
            error_summary=error_summary,
        )

    def _update_state(
        self,
        context: RuntimeContext,
        *,
        current_phase: str | None = None,
        active_agent: str | None = None,
        pending_nodes: list[str] | None = None,
        completed_nodes: list[str] | None = None,
        last_event_id: str | None = None,
    ) -> None:
        self._compat().update_state(
            context,
            current_phase=current_phase,
            active_agent=active_agent,
            pending_nodes=pending_nodes,
            completed_nodes=completed_nodes,
            last_event_id=last_event_id,
        )

    def _build_response(
        self,
        *,
        request: RequestEnvelope,
        run_id: str,
        intent: str,
        status: str,
        message: str,
        result: dict | None,
        phase: str | None,
        active_agent: str | None,
        latest_event: str | None,
        percent: int,
        errors: list[str] | None = None,
    ) -> ResponseEnvelope:
        return self._compat().build_response(
            request=request,
            run_id=run_id,
            intent=intent,
            status=status,
            message=message,
            result=result,
            phase=phase,
            active_agent=active_agent,
            latest_event=latest_event,
            percent=percent,
            errors=errors,
        )

    def _load_result_payload(self, result_path: Path) -> dict | None:
        return self._compat().load_result_payload(result_path)

    def _load_json_payload(self, path: Path) -> dict | None:
        return self._compat().load_json_payload(path)

    def _extend_subagent_trace(self, context: RuntimeContext, *, agent: str, phase: str, payload: dict | None) -> None:
        self._compat().extend_subagent_trace(context, agent=agent, phase=phase, payload=payload)

    def _handle_subagent_registry_event(
        self,
        *,
        stage: str,
        invocation,
        record,
        outputs: dict | None,
        error: Exception | None,
    ) -> None:
        self._compat().handle_subagent_registry_event(
            stage=stage,
            invocation=invocation,
            record=record,
            outputs=outputs,
            error=error,
        )

    def _build_debug_payload(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        plan_intent: str,
        retrieval_summary: str | None,
    ) -> dict:
        return self._research_forwarding().build_debug_payload(
            context=context,
            request=request,
            plan=plan,
            plan_intent=plan_intent,
            retrieval_summary=retrieval_summary,
        )

    def _replan_action_plan(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        current_plan: ActionPlan,
        plan_patch,
    ) -> ActionPlan:
        planner = self.planner
        bind_runtime = getattr(planner, "bind_runtime", None)
        clear_runtime = getattr(planner, "clear_runtime", None)
        if callable(bind_runtime):
            bind_runtime(
                context=context,
                support=self._execution_support(),
                request_id=request.request_id,
                intent=current_plan.intent,
            )
        try:
            return planner.replan_action_plan(
                intent=current_plan.intent,
                request=request,
                current_plan=current_plan,
                plan_patch=plan_patch,
            )
        finally:
            if callable(clear_runtime):
                clear_runtime()

    def _trace_task_name(self, payload: dict) -> str:
        return self._compat().trace_task_name(payload)

    def _trace_agent_path(self, payload: dict) -> str:
        return self._compat().trace_agent_path(payload)

    def _trace_agent_label(self, payload: dict) -> str:
        return self._compat().trace_agent_label(payload)

    def _enforce_runtime_controls(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        self._compat().enforce_runtime_controls(context, request_id=request_id, intent=intent)

    def _build_candidate_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
        candidate: dict,
        source_type: str,
    ) -> list[BlackboardEntry]:
        return self._research_forwarding().build_candidate_methodology_entries(
            context=context,
            request=request,
            phase=phase,
            agent=agent,
            candidate=candidate,
            source_type=source_type,
        )

    def _build_stock_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
    ) -> list[BlackboardEntry]:
        return self._research_forwarding().build_stock_methodology_entries(
            context=context,
            request=request,
            intent=intent,
            phase=phase,
            agent=agent,
        )

    def _build_review_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
        review_request: dict,
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
    ) -> list[BlackboardEntry]:
        return self._research_forwarding().build_review_methodology_entries(
            context=context,
            request=request,
            intent=intent,
            phase=phase,
            agent=agent,
            review_request=review_request,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
        )

    def _run_methodology_evolution_collect(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
    ) -> list[BlackboardEntry]:
        return self._research_forwarding().run_methodology_evolution_collect(
            context=context,
            request=request,
            phase=phase,
            agent=agent,
        )

    def _persist_proposal_transition(
        self,
        proposal,
        *,
        previous_status: str,
        apply_approved_side_effects: bool,
    ) -> dict[str, object]:
        return self._research_forwarding().persist_proposal_transition(
            proposal,
            previous_status=previous_status,
            apply_approved_side_effects=apply_approved_side_effects,
        )

    def _evaluate_methodology_auto_approval(
        self,
        *,
        proposal,
        constraints: dict,
    ) -> dict[str, object]:
        return self._research_forwarding().evaluate_methodology_auto_approval(
            proposal=proposal,
            constraints=constraints,
        )

    def _stale_memory_support_reason(
        self,
        *,
        proposal,
        max_age_days: int,
    ) -> str | None:
        return self._research_forwarding().stale_memory_support_reason(
            proposal=proposal,
            max_age_days=max_age_days,
        )

    def _insufficient_memory_support_reason(
        self,
        *,
        proposal,
        min_entity_scope_count: int,
    ) -> str | None:
        return self._research_forwarding().insufficient_memory_support_reason(
            proposal=proposal,
            min_entity_scope_count=min_entity_scope_count,
        )

    def _append_proposal_review_event(
        self,
        proposal,
        *,
        decision: str,
        reviewer: str,
        reason: str,
        previous_status: str | None,
        reviewed_at,
        source: str,
    ) -> None:
        self._research_forwarding().append_proposal_review_event(
            proposal,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            previous_status=previous_status,
            reviewed_at=reviewed_at,
            source=source,
        )

    def _resolve_methodology_target_run_ids(self, request: RequestEnvelope) -> list[str]:
        return self._research_forwarding().resolve_methodology_target_run_ids(request)

    def _wait_for_external_dependency(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        self._compat().wait_for_external_dependency(context, request_id=request_id, intent=intent)
