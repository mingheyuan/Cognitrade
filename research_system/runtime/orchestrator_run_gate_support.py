from __future__ import annotations

from typing import Any


class OrchestratorRunGateSupport:
    def __init__(self, host: Any) -> None:
        self.host = host

    def emit_gate_verdicts(
        self,
        *,
        context,
        request,
        run_id: str,
        intent: str,
    ) -> list:
        verdicts = self.host.gates.evaluate(
            run_id=run_id,
            phase="report",
            entries=context.blackboard.list_entries(),
            requires_verified_snapshot=intent != "methodology_evolution",
            requires_review_findings=intent == "review_thesis",
        )
        for verdict in verdicts:
            self.host._emit_event(
                context,
                request_id=request.request_id,
                intent=intent,
                event_type="phase_completed",
                phase="gate",
                message=f"{verdict.gate_type}: {verdict.verdict}",
                payload=verdict.model_dump(mode="json"),
            )
        return [verdict for verdict in verdicts if verdict.verdict == "fail"]


__all__ = ["OrchestratorRunGateSupport"]
