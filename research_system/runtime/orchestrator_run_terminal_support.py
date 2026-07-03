from __future__ import annotations

from typing import Any

from research_system.runtime.orchestrator_run_failure_support import (
    OrchestratorRunFailureSupport,
)
from research_system.runtime.orchestrator_run_gate_support import OrchestratorRunGateSupport
from research_system.runtime.orchestrator_run_response_support import (
    OrchestratorRunResponseSupport,
)
from research_system.runtime.orchestrator_run_success_support import (
    OrchestratorRunSuccessSupport,
)
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


class OrchestratorRunTerminalSupport:
    def __init__(self, host: Any) -> None:
        self.host = host
        self.gate_support = OrchestratorRunGateSupport(host)
        self.response_support = OrchestratorRunResponseSupport(host)
        self.success_support = OrchestratorRunSuccessSupport(host, self.gate_support, self.response_support)
        self.failure_support = OrchestratorRunFailureSupport(host, self.response_support)

    def finalize_success_path(
        self,
        *,
        context,
        request: RequestEnvelope,
        run_id: str,
        intent: str,
        result_path,
    ) -> ResponseEnvelope:
        return self.success_support.finalize_success_path(
            context=context,
            request=request,
            run_id=run_id,
            intent=intent,
            result_path=result_path,
        )

    def build_cancelled_response(
        self,
        *,
        context,
        request: RequestEnvelope,
        run_id: str,
        intent: str,
    ) -> ResponseEnvelope:
        return self.failure_support.build_cancelled_response(
            context=context,
            request=request,
            run_id=run_id,
            intent=intent,
        )

    def build_timed_out_response(
        self,
        *,
        context,
        request: RequestEnvelope,
        run_id: str,
        intent: str,
    ) -> ResponseEnvelope:
        return self.failure_support.build_timed_out_response(
            context=context,
            request=request,
            run_id=run_id,
            intent=intent,
        )

    def build_failed_response(
        self,
        *,
        context,
        request: RequestEnvelope,
        run_id: str,
        intent: str,
        error: Exception,
    ) -> ResponseEnvelope:
        return self.failure_support.build_failed_response(
            context=context,
            request=request,
            run_id=run_id,
            intent=intent,
            error=error,
        )
