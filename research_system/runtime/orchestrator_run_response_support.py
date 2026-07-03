from __future__ import annotations

from typing import Any

from research_system.schemas.protocol import ResponseEnvelope


class OrchestratorRunResponseSupport:
    def __init__(self, host: Any) -> None:
        self.host = host

    def build_success_result(self, *, intent: str, result_path) -> dict:
        return {
            "intent": intent,
            "report": self.host.runtime_io.load_result_payload(result_path),
        }

    def finalize_terminal_response(
        self,
        *,
        context,
        request,
        run_id: str,
        intent: str,
        status: str,
        message: str,
        result: dict,
        summary: dict,
        phase: str | None,
        active_agent: str | None,
        latest_event: str | None,
        percent: int,
        errors: list[str],
        success: bool,
    ) -> ResponseEnvelope:
        context.session_history.write_result(result, summary)
        if success:
            self.host.retention.apply_on_success(context.paths.root)
        else:
            self.host.retention.apply_on_terminal_failure(context.paths.root)
        return self.host.runtime_io.build_response(
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


__all__ = ["OrchestratorRunResponseSupport"]
