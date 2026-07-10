from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.schemas.protocol import ClarificationPayload, RequestEnvelope


class TaskIntakeDecision(BaseModel):
    """A domain-pack decision about how an unstructured request should run."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "needs_clarification", "unmatched"]
    intent: str | None = None
    domain_id: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    clarification: ClarificationPayload | None = None
    trace: dict[str, Any] = Field(default_factory=dict)

    def apply_to(self, request: RequestEnvelope) -> RequestEnvelope:
        if self.status != "accepted" or not self.intent:
            raise ValueError("only an accepted Task Intake decision can resolve a request")
        return request.model_copy(
            update={
                "intent": self.intent,
                "slots": {**dict(request.slots), **self.slots},
                "constraints": {**dict(request.constraints), **self.constraints},
                "clarification_status": "resolved" if request.clarification_status == "pending" else request.clarification_status,
            }
        )


__all__ = ["TaskIntakeDecision"]
