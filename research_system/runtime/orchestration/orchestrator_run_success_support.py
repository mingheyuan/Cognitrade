from __future__ import annotations

from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


class OrchestratorRunSuccessSupport:
    def __init__(self, host, response_support) -> None:
        self.host = host
        self.response_support = response_support

    def finalize_success_path(
        self,
        *,
        context,
        request: RequestEnvelope,
        run_id: str,
        intent: str,
        result_path,
    ) -> ResponseEnvelope:
        self.host._enforce_run_controls(context, request_id=request.request_id, intent=intent)
        self.host._transition(
            context,
            "gated",
            request_id=request.request_id,
            intent=intent,
            message="Evaluating run gates",
        )
        snapshot = read_json(context.paths.root / "execution_snapshot.json") or {}
        blocked = [
            row
            for row in snapshot.get("gate_evaluations", [])
            if row.get("blocking") and row.get("verdict") != "pass"
        ]
        if blocked:
            context.thread.status = "failed"
            context.turn.status = "failed"
            write_json(context.paths.thread_state, context.thread.model_dump(mode="json"))
            write_json(context.paths.turn_state, context.turn.model_dump(mode="json"))
            result = {
                "intent": intent,
                "status": "failed",
                "gate_failures": [
                    verdict.model_dump(mode="json") if hasattr(verdict, "model_dump") else verdict
                    for verdict in blocked
                ],
            }
            summary = {
                "intent": intent,
                "status": "failed",
                "message": "Run failed gate validation",
            }
            self.host._transition(
                context,
                "failed",
                request_id=request.request_id,
                intent=intent,
                message="One or more gates failed",
            )
            return self.response_support.finalize_terminal_response(
                context=context,
                request=request,
                run_id=run_id,
                intent=intent,
                status="failed",
                message="Run failed gate validation",
                result=result,
                summary=summary,
                phase="gate",
                active_agent=None,
                latest_event="run_failed",
                percent=100,
                errors=["gate_validation_failed"],
                success=False,
            )

        self.host._transition(
            context,
            "completed",
            request_id=request.request_id,
            intent=intent,
            message="Run completed",
        )
        context.thread.status = "completed"
        context.turn.status = "completed"
        write_json(context.paths.thread_state, context.thread.model_dump(mode="json"))
        write_json(context.paths.turn_state, context.turn.model_dump(mode="json"))
        summary = {
            "intent": intent,
            "status": "completed",
            "message": "Run completed successfully",
        }
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="run_completed",
            message="Run completed",
        )
        return self.response_support.finalize_terminal_response(
            context=context,
            request=request,
            run_id=run_id,
            intent=intent,
            status="completed",
            message="Run completed",
            result=self.response_support.build_success_result(intent=intent, result_path=result_path),
            summary=summary,
            phase="report",
            active_agent="reporter",
            latest_event="run_completed",
            percent=100,
            errors=[],
            success=True,
        )
