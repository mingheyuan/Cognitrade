from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.schemas.artifacts import ArtifactDraft


FailureInjectionKind = Literal["capability", "tool", "gate"]
EvaluationMode = Literal["contract", "execution"]


class FailureInjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FailureInjectionKind
    target: str
    count: int = Field(default=1, ge=1)
    reason: str = "evaluation failure injection"

    @model_validator(mode="after")
    def validate_injection(self) -> "FailureInjection":
        self.target = self.target.strip()
        self.reason = self.reason.strip()
        if not self.target:
            raise ValueError("failure injection target is required")
        if not self.reason:
            raise ValueError("failure injection reason is required")
        return self


class PackEvaluationDefinition(BaseModel):
    """Declarative smoke or failure-recovery evaluation owned by a Pack."""

    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    mode: EvaluationMode = "contract"
    objective: str | None = None
    intent: str | None = None
    available_artifact_types: list[str] = Field(default_factory=list)
    required_artifact_types: list[str] = Field(default_factory=list)
    required_artifact_type: str | None = None
    seed_artifacts: list[ArtifactDraft] = Field(default_factory=list)
    required_gate_id: str | None = None
    expected_status: Literal[
        "completed",
        "gated",
        "failed",
        "cancelled",
        "timed_out",
        "not_run",
    ] = "completed"
    expected_gate_verdicts: dict[str, Literal["pass", "fail", "needs_review"]] = Field(
        default_factory=dict
    )
    required_event_types: list[str] = Field(default_factory=list)
    forbidden_event_types: list[str] = Field(default_factory=list)
    expected_fallback_capabilities: list[str] = Field(default_factory=list)
    failure_injections: list[FailureInjection] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_definition(self) -> "PackEvaluationDefinition":
        self.evaluation_id = self.evaluation_id.strip()
        if not self.evaluation_id:
            raise ValueError("Pack evaluation_id is required")
        if self.objective is None:
            self.objective = self.evaluation_id
        self.objective = self.objective.strip()
        if not self.objective:
            raise ValueError("Pack evaluation objective is required")
        if self.required_artifact_type:
            self.required_artifact_types = list(
                dict.fromkeys([*self.required_artifact_types, self.required_artifact_type])
            )
        self.required_artifact_types = list(
            dict.fromkeys(str(item).strip() for item in self.required_artifact_types if str(item).strip())
        )
        self.available_artifact_types = list(
            dict.fromkeys(str(item).strip() for item in self.available_artifact_types if str(item).strip())
        )
        if not self.available_artifact_types:
            self.available_artifact_types = list(
                dict.fromkeys(item.artifact_type for item in self.seed_artifacts)
            )
        if self.mode == "execution" and not self.required_artifact_types:
            raise ValueError("execution Pack evaluation requires required_artifact_types")
        if self.mode == "execution" and not self.seed_artifacts and self.available_artifact_types:
            raise ValueError("execution Pack evaluation requires seed_artifacts")
        return self


class PackEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    domain_id: str
    passed: bool
    executed: bool
    status: str
    failures: list[str] = Field(default_factory=list)
    gate_verdicts: dict[str, str] = Field(default_factory=dict)
    event_types: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "EvaluationMode",
    "FailureInjection",
    "FailureInjectionKind",
    "PackEvaluationDefinition",
    "PackEvaluationResult",
]
