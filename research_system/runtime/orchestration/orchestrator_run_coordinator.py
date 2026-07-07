from __future__ import annotations

from typing import Any

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
            self.host._execute_plan(context=context, request=request, plan=plan)
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
