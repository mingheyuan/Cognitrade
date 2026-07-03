from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext


class OrchestratorExecutionControlSupport:
    def __init__(self, host: Any, *, lifecycle_support) -> None:
        self.host = host
        self.lifecycle_support = lifecycle_support

    def enforce_runtime_controls(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        if context.control_plane.cancellation_requested():
            self.lifecycle_support.transition(
                context,
                "cancelled",
                request_id=request_id,
                intent=intent,
                message="Run cancelled by user",
            )
            self.lifecycle_support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="run_cancelled",
                message="Run cancelled",
            )
            raise self.host.run_cancelled_error_cls("run_cancelled")
        if context.control_plane.run_timed_out():
            self.lifecycle_support.transition(
                context,
                "timed_out",
                request_id=request_id,
                intent=intent,
                message="Run timed out",
            )
            self.lifecycle_support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="run_timed_out",
                message="Run timed out",
            )
            raise self.host.run_timed_out_error_cls("run_timed_out")

    def wait_for_external_dependency(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        import time

        while True:
            if context.control_plane.cancellation_requested():
                self.lifecycle_support.transition(
                    context,
                    "cancelled",
                    request_id=request_id,
                    intent=intent,
                    message="Run cancelled while waiting",
                )
                self.lifecycle_support.emit_event(
                    context,
                    request_id=request_id,
                    intent=intent,
                    event_type="run_cancelled",
                    message="Run cancelled while waiting",
                )
                raise self.host.run_cancelled_error_cls("run_cancelled")
            if context.control_plane.external_wait_timed_out() or context.control_plane.run_timed_out():
                self.lifecycle_support.transition(
                    context,
                    "timed_out",
                    request_id=request_id,
                    intent=intent,
                    message="External wait timed out",
                )
                self.lifecycle_support.emit_event(
                    context,
                    request_id=request_id,
                    intent=intent,
                    event_type="run_timed_out",
                    message="External wait timed out",
                )
                raise self.host.run_timed_out_error_cls("run_timed_out")
            time.sleep(0.2)
