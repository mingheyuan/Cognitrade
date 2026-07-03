from __future__ import annotations

from typing import Any

from research_system.runtime.orchestrator_run_startup_support import (
    OrchestratorRunStartupSupport,
)
from research_system.runtime.orchestrator_run_terminal_support import (
    OrchestratorRunTerminalSupport,
)
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


class OrchestratorRunCoordinator:
    def __init__(self, host: Any) -> None:
        self.host = host
        self.startup_support = OrchestratorRunStartupSupport(host)
        self.terminal_support = OrchestratorRunTerminalSupport(host)

    def run(self, request: RequestEnvelope) -> ResponseEnvelope:
        decision = self.host.task_router.identify(request)
        if decision.trace and isinstance(request.constraints, dict):
            request.constraints["routing_trace"] = dict(decision.trace)
        if decision.intent is None:
            return self.startup_support.build_clarification_response(
                request=request,
                decision=decision,
            )

        intent = decision.intent
        run_id, paths, context = self.startup_support.create_context(
            request=request,
            intent=intent,
        )
        self.host._active_runtime_context = context

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
