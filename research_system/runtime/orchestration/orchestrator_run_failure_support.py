from __future__ import annotations

from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


class OrchestratorRunFailureSupport:
    def __init__(self, host, response_support) -> None:
        self.host = host
        self.response_support = response_support

    def build_cancelled_response(
        self,
        *,
        context,
        request: RequestEnvelope,
        run_id: str,
        intent: str,
    ) -> ResponseEnvelope:
        context.thread.status = "cancelled"
        context.turn.status = "cancelled"
        write_json(context.paths.thread_state, context.thread.model_dump(mode="json"))
        write_json(context.paths.turn_state, context.turn.model_dump(mode="json"))
        result = {"intent": intent, "status": "cancelled"}
        summary = {"intent": intent, "status": "cancelled", "message": "Run cancelled"}
        return self.response_support.finalize_terminal_response(
            context=context,
            request=request,
            run_id=run_id,
            intent=intent,
            status="cancelled",
            message="Run cancelled",
            result=result,
            summary=summary,
            phase=context.state.current_phase,
            active_agent=context.state.active_agent,
            latest_event="run_cancelled",
            percent=100,
            errors=[],
            success=False,
        )

    def build_timed_out_response(
        self,
        *,
        context,
        request: RequestEnvelope,
        run_id: str,
        intent: str,
    ) -> ResponseEnvelope:
        context.thread.status = "failed"
        context.turn.status = "failed"
        write_json(context.paths.thread_state, context.thread.model_dump(mode="json"))
        write_json(context.paths.turn_state, context.turn.model_dump(mode="json"))
        result = {"intent": intent, "status": "timed_out"}
        summary = {"intent": intent, "status": "timed_out", "message": "Run timed out"}
        return self.response_support.finalize_terminal_response(
            context=context,
            request=request,
            run_id=run_id,
            intent=intent,
            status="timed_out",
            message="Run timed out",
            result=result,
            summary=summary,
            phase=context.state.current_phase,
            active_agent=context.state.active_agent,
            latest_event="run_timed_out",
            percent=100,
            errors=["run_timed_out"],
            success=False,
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
        self.host._transition(
            context,
            "failed",
            request_id=request.request_id,
            intent=intent,
            message="Run failed unexpectedly",
            error_summary=str(error),
        )
        context.thread.status = "failed"
        context.turn.status = "failed"
        write_json(context.paths.thread_state, context.thread.model_dump(mode="json"))
        write_json(context.paths.turn_state, context.turn.model_dump(mode="json"))
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="run_failed",
            message="Run failed",
            payload={"error": str(error)},
        )
        context.logger.log(run_id, "ERROR", "orchestrator", str(error))
        result = {"intent": intent, "status": "failed"}
        summary = {"intent": intent, "status": "failed", "message": str(error)}
        return self.response_support.finalize_terminal_response(
            context=context,
            request=request,
            run_id=run_id,
            intent=intent,
            status="failed",
            message=str(error),
            result=result,
            summary=summary,
            phase=context.state.current_phase,
            active_agent=context.state.active_agent,
            latest_event="run_failed",
            percent=100,
            errors=[str(error)],
            success=False,
        )
