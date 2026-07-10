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
    """Coordinates the canonical Agent OS request lifecycle."""

    def __init__(self, host: Any) -> None:
        self.host = host
        self.startup_support = OrchestratorRunStartupSupport(host)
        self.terminal_support = OrchestratorRunTerminalSupport(host)

    def run(self, request: RequestEnvelope) -> ResponseEnvelope:
        run_id, paths, context = self.startup_support.create_preflight_context(request=request)
        self.host._active_runtime_context = context
        decision = self.host.task_router.identify(request)
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
        try:
            graph = self.startup_support.initialize_run(
                context=context,
                request=request,
                intent=intent,
            )
            self.host._execute_plan(context=context, request=request, plan=graph)
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
        except Exception as exc:
            return self.terminal_support.build_failed_response(
                context=context,
                request=request,
                run_id=run_id,
                intent=intent,
                error=exc,
            )
        finally:
            self.host._active_runtime_context = None


__all__ = ["OrchestratorRunCoordinator"]
