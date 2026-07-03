from __future__ import annotations

from pathlib import Path

from research_system.runtime.jsonl import read_json
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


class OrchestratorRuntimeIoSupport:
    def __init__(self, host) -> None:
        self.host = host

    def build_response(
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
        progress = self.host.progress_service.build(
            current_phase=phase,
            active_agent=active_agent,
            latest_event=latest_event,
            status_message=message,
            percent=percent,
        )
        return ResponseEnvelope(
            request_id=request.request_id,
            run_id=run_id,
            intent=intent,
            status=status,  # type: ignore[arg-type]
            message=message,
            progress=progress,
            result=result,
            errors=errors or [],
        )

    def load_result_payload(self, result_path: Path) -> dict | None:
        return self.read_json_payload(result_path)

    def load_json_payload(self, path: Path) -> dict | None:
        return self.read_json_payload(path)

    @staticmethod
    def read_json_payload(path: Path) -> dict | None:
        payload = read_json(path)
        return payload if isinstance(payload, dict) else None
