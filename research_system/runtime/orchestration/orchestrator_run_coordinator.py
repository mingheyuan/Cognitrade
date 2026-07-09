from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.dag_scheduler import ActionDagScheduler
from research_system.runtime.orchestration.orchestrator_run_startup_support import (
    OrchestratorRunStartupSupport,
)
from research_system.runtime.orchestration.orchestrator_run_terminal_support import (
    OrchestratorRunTerminalSupport,
)
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


class OrchestratorRunCoordinator:
    def __init__(self, host: Any) -> None:
        self.host = host
        self.startup_support = OrchestratorRunStartupSupport(host)
        self.terminal_support = OrchestratorRunTerminalSupport(host)
        self.dag_scheduler = ActionDagScheduler()

    def run(self, request: RequestEnvelope) -> ResponseEnvelope:
        run_id, paths, context = self.startup_support.create_preflight_context(request=request)
        self.host._active_runtime_context = context
        bind_runtime = getattr(self.host.task_router, "bind_runtime", None)
        clear_runtime = getattr(self.host.task_router, "clear_runtime", None)
        if callable(bind_runtime):
            bind_runtime(
                context=context,
                support=self.host._execution_support(),
                request_id=request.request_id,
            )
        try:
            decision = self.host.task_router.identify(request)
        finally:
            if callable(clear_runtime):
                clear_runtime()
        if decision.trace and isinstance(request.constraints, dict):
            request.constraints["routing_trace"] = dict(decision.trace)
        if decision.intent is None:
            try:
                return self.startup_support.build_clarification_response(
                    request=request,
                    decision=decision,
                    run_id=run_id,
                )
            finally:
                self.host._active_runtime_context = None

        intent = decision.intent
        self.startup_support.promote_context_intent(
            context=context,
            request=request,
            intent=intent,
        )
        self.host._attach_subagent_observers()

        try:
            plan = self.startup_support.initialize_run(
                context=context,
                request=request,
                intent=intent,
            )
            runtime_control = getattr(self.host, "runtime_control", None) or self.host.supervisor
            bind_runtime = getattr(runtime_control, "bind_runtime", None)
            clear_runtime = getattr(runtime_control, "clear_runtime", None)
            if callable(bind_runtime):
                bind_runtime(
                    context=context,
                    support=self.host._execution_support(),
                    request_id=request.request_id,
                    intent=intent,
                )
            try:
                self.host._execute_plan(context=context, request=request, plan=plan)
            finally:
                if callable(clear_runtime):
                    clear_runtime()
            return self.terminal_support.finalize_success_path(
                context=context,
                request=request,
                run_id=run_id,
                intent=intent,
                result_path=paths.result,
            )
        except self.host.run_cancelled_error_cls:
            return self.terminal_support.build_cancelled_response(
                context=context,
                request=request,
                run_id=run_id,
                intent=intent,
            )
        except self.host.run_timed_out_error_cls:
            return self.terminal_support.build_timed_out_response(
                context=context,
                request=request,
                run_id=run_id,
                intent=intent,
            )
        except Exception as exc:  # pragma: no cover - final guardrail
            return self.terminal_support.build_failed_response(
                context=context,
                request=request,
                run_id=run_id,
                intent=intent,
                error=exc,
            )
        finally:
            self.host._active_runtime_context = None

    def rerun_from(self, source_run_id: str, *, from_node: str) -> ResponseEnvelope:
        return self._rerun_existing_plan(
            source_run_id=source_run_id,
            from_node=from_node,
        )

    def rerun_subagent_from(
        self,
        source_run_id: str,
        *,
        action_node: str,
        dag_id: str,
        from_node: str,
    ) -> ResponseEnvelope:
        return self._rerun_existing_plan(
            source_run_id=source_run_id,
            from_node=action_node,
            rerun_subagent_dag_id=dag_id,
            rerun_subagent_from_node=from_node,
        )

    def _rerun_existing_plan(
        self,
        *,
        source_run_id: str,
        from_node: str,
        rerun_subagent_dag_id: str | None = None,
        rerun_subagent_from_node: str | None = None,
    ) -> ResponseEnvelope:
        source_paths, request, plan, source_thread_payload = self._load_rerun_source(source_run_id)
        if not any(action.node_id == from_node for action in plan.actions):
            raise self.host.orchestrator_error_cls(f"unknown_rerun_node:{from_node}")
        rerun_nodes = {from_node, *self.dag_scheduler.descendants(plan, from_node)}
        completed_nodes = [
            action.node_id
            for action in plan.actions
            if action.node_id not in rerun_nodes
        ]

        run_id, paths, context = self.startup_support.create_context(request=request, intent=plan.intent)
        context.cache_restore_run_root = source_paths.root
        context.cache_restore_thread_id = str(source_thread_payload.get("thread_id") or "").strip() or None
        context.rerun_from_node_id = from_node
        if rerun_subagent_dag_id and rerun_subagent_from_node:
            context.rerun_subagent_action_node_id = from_node
            context.rerun_subagent_dag_id = rerun_subagent_dag_id
            context.rerun_subagent_from_node_id = rerun_subagent_from_node
        context.state.completed_nodes = completed_nodes
        self.host._active_runtime_context = context
        self.host._attach_subagent_observers()

        try:
            self.startup_support.initialize_existing_plan(
                context=context,
                request=request,
                intent=plan.intent,
                plan=plan,
                source_run_id=source_run_id,
                rerun_from_node=from_node,
                rerun_subagent_dag_id=rerun_subagent_dag_id,
                rerun_subagent_from_node=rerun_subagent_from_node,
            )
            self.host._update_state(
                context,
                pending_nodes=[action.node_id for action in plan.actions if action.node_id in rerun_nodes],
                completed_nodes=completed_nodes,
            )
            runtime_control = getattr(self.host, "runtime_control", None) or self.host.supervisor
            bind_runtime = getattr(runtime_control, "bind_runtime", None)
            clear_runtime = getattr(runtime_control, "clear_runtime", None)
            if callable(bind_runtime):
                bind_runtime(
                    context=context,
                    support=self.host._execution_support(),
                    request_id=request.request_id,
                    intent=plan.intent,
                )
            try:
                self.host._execute_plan(context=context, request=request, plan=plan)
            finally:
                if callable(clear_runtime):
                    clear_runtime()
            return self.terminal_support.finalize_success_path(
                context=context,
                request=request,
                run_id=run_id,
                intent=plan.intent,
                result_path=paths.result,
            )
        except self.host.run_cancelled_error_cls:
            return self.terminal_support.build_cancelled_response(
                context=context,
                request=request,
                run_id=run_id,
                intent=plan.intent,
            )
        except self.host.run_timed_out_error_cls:
            return self.terminal_support.build_timed_out_response(
                context=context,
                request=request,
                run_id=run_id,
                intent=plan.intent,
            )
        except Exception as exc:  # pragma: no cover - final guardrail
            return self.terminal_support.build_failed_response(
                context=context,
                request=request,
                run_id=run_id,
                intent=plan.intent,
                error=exc,
            )
        finally:
            self.host._active_runtime_context = None

    def _load_rerun_source(
        self,
        source_run_id: str,
    ) -> tuple[Any, RequestEnvelope, Any, dict[str, Any]]:
        source_paths = self.host.run_store.load_run_paths(source_run_id)
        request_payload = self.host._load_json_payload(source_paths.request)
        if not isinstance(request_payload, dict):
            raise self.host.orchestrator_error_cls(f"missing_request_payload:{source_run_id}")
        task_plan_payload = self.host._load_json_payload(source_paths.task_plan)
        if not isinstance(task_plan_payload, dict):
            raise self.host.orchestrator_error_cls(f"missing_task_plan_payload:{source_run_id}")
        source_thread_payload = self.host._load_json_payload(source_paths.thread_state) or {}
        request = RequestEnvelope.model_validate(request_payload)
        plan = self.host._assembly().plan_from_payload(task_plan_payload)
        return source_paths, request, plan, source_thread_payload
